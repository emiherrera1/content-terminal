"""Video-level classification and topic rollups, offline (synthetic data, fake model)."""
import datetime as dt
import json

import pytest

from terminal import content_index, ctncs, metrics, store, video_classify

DAY = dt.date(2026, 10, 18)
WOOD = "02.05.10.19"          # Education > Skills & DIY > Crafting Guides > Woodworking
FOOD = "03.08.15"             # Lifestyle > Food & Cooking > Recipes (a level-3 code)


@pytest.fixture
def con():
    con = store.connect(":memory:")
    con.execute("INSERT INTO channels (channel_id, title) VALUES ('UCw', 'Shop Time'), ('UCh', 'Home Fixers')")
    # (video, channel, published, duration s, topic, format, daily gain)
    vids = [("w1", "UCw", DAY - dt.timedelta(days=30), 900, WOOD, "Tutorials", 1000),
            ("w2", "UCw", DAY - dt.timedelta(days=20), 600, WOOD, "Reviews", 500),
            ("h1", "UCh", DAY - dt.timedelta(days=10), 60, WOOD, "DIY", 200),   # woodworking from a non-woodworking channel
            ("h2", "UCh", DAY - dt.timedelta(days=10), 700, FOOD, "Recipes", 300),
            ("x1", "UCh", DAY - dt.timedelta(days=10), 700, "unclassified", None, 999)]
    book = ctncs.load()
    fid = {v.lower(): k for k, v in ctncs.formats(book).items()}
    for vid, ch, pub, dur, code, fmt, g in vids:
        con.execute("INSERT INTO videos (video_id, channel_id, title, published_at, duration_s) VALUES (?, ?, ?, ?, ?)",
                    [vid, ch, f"video {vid}", pub, dur])
        parts = code.split(".") if code != "unclassified" else []
        con.execute("INSERT INTO video_classifications (video_id, topic_code, sector, category, niche, subniche, "
                    "format_id, format_label, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.9)",
                    [vid, code] + [".".join(parts[:i]) if len(parts) >= i else None for i in (1, 2, 3, 4)]
                    + [fid.get((fmt or "").lower()), fmt])
        for i in range(9):  # 9 days of snapshots; woodworking tutorial doubles on the last day
            d = DAY - dt.timedelta(days=8 - i)
            total = 10_000 + g * i + (1000 if vid == "w1" and i == 8 else 0)
            con.execute("INSERT INTO video_snapshots VALUES (?, ?, ?, 0, 0)", [d, vid, total])
    # a brand-new upload yesterday: its first snapshot's views all count as gained today
    con.execute("INSERT INTO videos (video_id, channel_id, title, published_at, duration_s) VALUES "
                "('w3', 'UCh', 'new build', ?, 1200)", [dt.datetime.combine(DAY - dt.timedelta(days=1), dt.time(18))])
    con.execute("INSERT INTO video_classifications (video_id, topic_code, sector, category, niche, subniche, format_id, "
                "format_label, confidence) VALUES ('w3', ?, '02', '02.05', '02.05.10', ?, NULL, NULL, 0.8)", [WOOD, WOOD])
    con.execute("INSERT INTO video_snapshots VALUES (?, 'w3', 4000, 0, 0)", [DAY])
    metrics.build(con)
    return con


def daily(con, code, day=DAY):
    return con.execute("SELECT views_gained, videos, channels, new_videos FROM content_daily "
                       "WHERE code = ? AND snapshot_date = ?", [code, day]).fetchone()


def test_video_counts_toward_its_own_topic_not_its_channel(con):
    views, videos, channels, new = daily(con, WOOD)
    assert views == 1000 + 1000 + 500 + 200 + 4000   # tutorial spike + review + other channel's DIY video + new upload
    assert (videos, channels, new) == (4, 2, 1)


def test_rolls_up_every_level_and_skips_unclassified(con):
    assert daily(con, "02.05.10")[0] == daily(con, WOOD)[0]
    assert daily(con, "02")[0] == daily(con, WOOD)[0]
    assert daily(con, FOOD)[0] == 300 and daily(con, "03")[0] == 300
    total = con.execute("SELECT sum(views_gained) FROM content_daily WHERE level = 1 AND snapshot_date = ?", [DAY]).fetchone()[0]
    assert total == 6700 + 300   # x1 (unclassified) is left out


def test_formats_split(con):
    f = dict(con.execute("SELECT format, views_gained FROM content_format_daily WHERE code = ? AND snapshot_date = ?",
                         [WOOD, DAY]).fetchall())
    assert f == {"Tutorials": 2000, "Reviews": 500, "DIY": 200, "Unspecified": 4000}


def test_index_report(con):
    hit = ctncs.resolve(ctncs.load(), "woodworking")[0]
    assert hit["code"] == WOOD
    r = content_index.report(con, WOOD)
    assert r["day"] == DAY and r["views_gained"] == 6700
    assert r["vs_yesterday"] == pytest.approx(6700 / 1700 - 1)   # yesterday: 1000 + 500 + 200
    assert r["vs_7day_avg"] == pytest.approx(6700 / 1700 - 1)
    assert r["top_videos"][0]["title"] == "new build" and r["top_videos"][0]["new"]
    assert "Woodworking" in content_index.render(r)
    sector = content_index.report(con, "02")
    assert sector["children"][0]["code"] == "02.05"


def test_parse_reply_validates_codes_and_formats():
    book = ctncs.load()
    text = 'Here: [{"id": "a", "topic": "02.05.10.19", "format": "tutorials", "confidence": 0.8},' \
           '{"id": "b", "topic": "99.99", "format": "Interpretive Dance", "confidence": 0.9}]'
    a, b, c = video_classify.parse_reply(text, ["a", "b", "c"], book)
    assert a["subniche"] == WOOD and a["format_label"] == "Tutorials"
    assert b["topic_code"] == "unclassified" and b["format_id"] is None
    assert c["topic_code"] == "unclassified" and c["confidence"] == 0   # missing from the reply


def test_classify_videos_batches_and_caches_codebook():
    con = store.connect(":memory:")
    con.execute("INSERT INTO channels (channel_id, title) VALUES ('UCw', 'Shop Time')")
    for i in range(30):
        con.execute("INSERT INTO videos (video_id, channel_id, title, published_at, duration_s) VALUES (?, 'UCw', ?, now(), 600)",
                    [f"v{i}", f"Build {i}"])

    class Fake:
        calls = []

        class messages:
            @staticmethod
            def create(**kw):
                Fake.calls.append(kw)
                ids = [line[4:] for line in kw["messages"][0]["content"].splitlines() if line.startswith("id: ")]
                body = json.dumps([{"id": i, "topic": WOOD, "format": "DIY", "confidence": 0.7} for i in ids])
                return type("M", (), {"content": [type("C", (), {"text": body})()]})()

    assert video_classify.classify_videos(con, client=Fake) == 30
    assert len(Fake.calls) == 2   # batches of 25
    assert Fake.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert con.execute("SELECT count(*) FROM video_classifications WHERE subniche = ?", [WOOD]).fetchone()[0] == 30
    assert video_classify.classify_videos(con, client=Fake) == 0   # each video is classified once
