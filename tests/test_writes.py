"""Bulk writes: the YouTube pull and the classifiers save in one statement, and re-runs replace rather than duplicate."""
import datetime as dt

from terminal import store, video_classify, youtube


def test_upsert_replaces_and_dedupes():
    con = store.connect(":memory:")
    cols = ["snapshot_date", "channel_id", "subscribers", "views", "video_count"]
    d = dt.date(2026, 11, 6)
    store.upsert(con, "channel_snapshots", cols, [[d, "UCa", 1, 10, 1], [d, "UCa", 2, 20, 2], [d, "UCb", 5, 50, 5]])
    store.upsert(con, "channel_snapshots", cols, [[d, "UCa", 3, 30, 3]])
    assert con.execute("SELECT channel_id, subscribers FROM channel_snapshots ORDER BY 1").fetchall() == [("UCa", 3), ("UCb", 5)]
    assert store.upsert(con, "channel_snapshots", cols, []) == 0


def test_pull_writes_channels_and_videos(monkeypatch):
    con = store.connect(":memory:")
    today = dt.date(2026, 11, 6)

    def fake_get(path, **p):
        if path == "channels":
            return {"items": [{"id": i, "snippet": {"title": f"T {i}", "country": "US", "publishedAt": "2020-01-01T00:00:00Z"},
                               "statistics": {"subscriberCount": "100", "viewCount": "1000", "videoCount": "3"},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU" + i[2:]}}} for i in p["id"].split(",")]}
        if path == "playlistItems":
            return {"items": [{"contentDetails": {"videoId": p["playlistId"] + f"-v{k}"}} for k in range(3)]}
        if path == "videos":
            return {"items": [{"id": v, "snippet": {"channelId": "UC" + v[2:].split("-")[0], "title": v, "publishedAt": "2026-11-01T00:00:00Z"},
                               "statistics": {"viewCount": "7"}, "contentDetails": {"duration": "PT5M"}} for v in p["id"].split(",")]}
        raise AssertionError(path)

    monkeypatch.setattr(youtube, "_get", fake_get)
    ids = [f"UC{k:03d}" for k in range(120)]                       # spans three API pages of 50
    assert youtube.pull_channels(con, ids, today) == 120
    assert youtube.pull_channels(con, ids, today) == 120          # same day again: replaced, not doubled
    assert con.execute("SELECT count(*) FROM channel_snapshots").fetchone()[0] == 120
    assert youtube.pull_recent_videos(con, today, 3) == 360
    assert con.execute("SELECT count(*), sum(views) FROM video_snapshots").fetchone() == (360, 2520)
    assert con.execute("SELECT min(duration_s) FROM videos").fetchone()[0] == 300


def test_video_classifier_runs_batches_in_parallel(monkeypatch):
    con = store.connect(":memory:")
    con.execute("INSERT INTO channels (channel_id, title) VALUES ('UCx', 'X')")
    now = dt.datetime.now()
    for k in range(60):
        con.execute("INSERT INTO videos (video_id, channel_id, title, published_at) VALUES (?, 'UCx', ?, ?)", [f"v{k}", f"Video {k}", now])

    class Fake:
        class messages:
            @staticmethod
            def create(**kw):
                return object()

    monkeypatch.setattr(video_classify.llm, "reply_text", lambda m: "")
    monkeypatch.setattr(video_classify.llm, "warn_if_cut", lambda m, s: None)
    monkeypatch.setattr(video_classify, "parse_reply", lambda text, ids, book: [
        {"video_id": i, "topic_code": "03.08.015", "sector": "03", "category": "03.08", "niche": "03.08.015", "subniche": None,
         "format_id": None, "format_label": None, "confidence": 0.9} for i in ids])
    assert video_classify.classify_videos(con, client=Fake()) == 60
    assert con.execute("SELECT count(*), bool_and(NOT reviewed) FROM video_classifications").fetchone() == (60, True)
    assert video_classify.classify_videos(con, client=Fake()) == 0    # nothing left to do
