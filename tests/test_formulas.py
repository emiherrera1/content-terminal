"""The Formula Library's "Next" set, checked against independent Python calculations on a synthetic world."""
import datetime as dt
import math

import pytest

from terminal import formulas, metrics, store

D0 = dt.date(2026, 10, 1)
LAST = 59                       # day index of the latest snapshot
WOOD, FOOD = "02.05.010.019", "03.08.015"
FMT = {"Tutorials": "56", "Reviews": "41", "DIY": "14", "Recipes": "39"}

# video: (channel, topic, format, publish day, views per day)
VIDEOS = {**{f"a{i}": ("UCa", WOOD, "Tutorials", 5 * i, 1000) for i in range(1, 7)},
          "a7": ("UCa", WOOD, "Reviews", 54, 10_000),                     # outlier, 5 days old on the last day
          **{f"b{i}": ("UCb", WOOD, "DIY", -100, 500) for i in range(1, 5)},
          "n1": ("UCn", WOOD, "Tutorials", 39, 2000),                     # a new channel entering woodworking
          **{f"f{i}": ("UCf", FOOD, "Recipes", -100, 1000) for i in range(1, 3)}}
CHANNEL_CREATED = {"UCa": dt.date(2018, 1, 1), "UCb": dt.date(2015, 1, 1), "UCf": dt.date(2016, 1, 1), "UCn": D0}


def day(i):
    return D0 + dt.timedelta(days=i)


def gain(vid, d):
    """Views gained on day d under the terminal's rules (first snapshot of an old video has no gain)."""
    _, _, _, pub, rate = VIDEOS[vid]
    first = max(pub, 0)
    return rate if d > first else None


def build_world(con):
    for ch, created in CHANNEL_CREATED.items():
        con.execute("INSERT INTO channels (channel_id, title, published_at, added_at) VALUES (?, ?, ?, ?)",
                    [ch, ch, created, day(0)])
    for vid, (ch, topic, fmt, pub, rate) in VIDEOS.items():
        p = topic.split(".")
        con.execute("INSERT INTO videos (video_id, channel_id, title, published_at, duration_s) VALUES (?, ?, ?, ?, 600)",
                    [vid, ch, vid, dt.datetime.combine(day(pub), dt.time(12))])
        con.execute("INSERT INTO video_classifications (video_id, topic_code, sector, category, niche, subniche, "
                    "format_id, format_label, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.9)",
                    [vid, topic, p[0], ".".join(p[:2]), ".".join(p[:3]), topic if len(p) == 4 else None, FMT[fmt], fmt])
        for d in range(max(pub, 0), LAST + 1):
            con.execute("INSERT INTO video_snapshots VALUES (?, ?, ?, 0, 0)", [day(d), vid, rate * (d - pub)])


@pytest.fixture
def con():
    con = store.connect(":memory:")
    build_world(con)
    metrics.build(con)
    formulas.build(con)
    return con


def topic_gain(d, topic=None, channels=None):
    return sum(g for v, (ch, t, *_ ) in VIDEOS.items() if (topic is None or t == topic)
               and (channels is None or ch in channels) and (g := gain(v, d)) is not None)


def test_attention_and_share(con):
    a, share = con.execute("SELECT a, share FROM topic_series WHERE code = '02' AND d = ?", [day(LAST)]).fetchone()
    assert a == topic_gain(LAST, WOOD)
    assert share == pytest.approx(topic_gain(LAST, WOOD) / topic_gain(LAST))


def test_new_creator_share_and_concentration(con):
    window = range(LAST - 27, LAST + 1)
    per = {ch: sum(topic_gain(d, WOOD, {ch}) for d in window) for ch in ("UCa", "UCb", "UCn")}
    total = sum(per.values())
    nc = con.execute("SELECT new_creator_share FROM topic_series WHERE code = ? AND d = ?", [WOOD, day(LAST)]).fetchone()[0]
    assert nc == pytest.approx(per["UCn"] / total)   # UCa and UCb are old channels, so they are not newcomers
    hhi, cr3, thin = con.execute("SELECT hhi, cr3, thin FROM topic_current WHERE code = ?", [WOOD]).fetchone()
    assert hhi == pytest.approx(sum((100 * x / total) ** 2 for x in per.values()))
    assert cr3 == pytest.approx(1.0) and thin   # 12 videos, 3 creators: below the reporting minimum


def expected_index(topic, start=1):
    """Chain-linked, creator-matched index computed independently."""
    def present(ch, d):
        return any(c == ch and max(p, 0) <= d for c, _, _, p, _ in VIDEOS.values())
    level = 100.0
    for d in range(start + 1, LAST + 1):
        matched = {ch for ch in CHANNEL_CREATED if present(ch, d) and present(ch, d - 1)}
        num, den = topic_gain(d, topic, matched), topic_gain(d - 1, topic, matched)
        if num and den:
            level *= num / den
    return level


def test_index_chain_links_and_relative_strength(con):
    rows = dict(con.execute("SELECT code, index_level FROM topic_index WHERE d = ?", [day(LAST)]).fetchall())
    assert rows[WOOD] == pytest.approx(expected_index(WOOD))
    assert rows["ALL"] == pytest.approx(expected_index(None))
    assert rows[FOOD] == pytest.approx(100)          # constant gains, constant index
    rs = con.execute("SELECT relative_strength FROM topic_index WHERE code = '02' AND d = ?", [day(LAST)]).fetchone()[0]
    assert rs == pytest.approx(rows["02"] / rows["ALL"] * 100)


def test_index_cap():
    con = store.connect(":memory:")
    build_world(con)
    metrics.build(con)
    cfg = formulas.config()
    cfg["thresholds"].update(creator_cap=0.5, cap_min_creators=2)
    formulas.build(con, cfg)
    # day 40: UCn's video gains for the first time. Raw: UCa 6x1000 + UCb 4x500 + UCn 2000 vs the day before without UCn.
    r = con.execute("SELECT ratio FROM topic_index WHERE code = ? AND d = ?", [WOOD, day(40)]).fetchone()[0]
    t = [6000, 2000, 2000]; y = [6000, 2000, 0]
    cap_t, cap_y = 0.5 * sum(t), 0.5 * sum(y)
    assert r == pytest.approx(sum(min(x, cap_t) for x in t) / sum(min(x, cap_y) for x in y))


def test_video_metrics_and_breakout(con):
    o, breakout, base, age = con.execute(
        "SELECT outlier_score, breakout, creator_baseline, age FROM video_metrics WHERE video_id = 'a7'").fetchone()
    assert age == 5 and base == 5 * 1000          # UCa's other videos at age 5
    assert o == pytest.approx(10) and breakout
    assert con.execute("SELECT breakout FROM video_metrics WHERE video_id = 'a1'").fetchone()[0] is False


def test_alerts(con):
    alerts = con.execute("SELECT kind, code, subject FROM topic_alerts").fetchall()
    assert ("video_breakout", WOOD, "a7") in alerts
    assert {c for k, c, s in alerts if k == "newcomer_win" and s == "UCn"} == {"02", "02.05", "02.05.010", WOOD}


def test_series_fields_exist(con):
    r = con.execute("SELECT wow, acceleration, weekday_factor, a_sa, dss, views_per_minute FROM topic_series "
                    "WHERE code = ? AND d = ?", [WOOD, day(LAST)]).fetchone()
    wow, acc, wf, a_sa, dss, vpm = r
    exp_wow = sum(topic_gain(d, WOOD) for d in range(LAST - 6, LAST + 1)) / \
        sum(topic_gain(d, WOOD) for d in range(LAST - 13, LAST - 6)) - 1
    assert wow == pytest.approx(exp_wow)
    assert 0.5 < wf < 2 and a_sa > 0 and dss is not None and vpm > 0 and acc is not None


def test_hygiene_negative_and_gap():
    con = store.connect(":memory:")
    con.execute("INSERT INTO videos (video_id, channel_id, title, published_at) VALUES ('v', 'UCx', 't', ?)", [day(-30)])
    for d, views in [(0, 100), (1, 200), (3, 500), (4, 450)]:
        con.execute("INSERT INTO video_snapshots VALUES (?, 'v', ?, 0, 0)", [day(d), views])
    metrics.build(con)
    rows = con.execute("SELECT snapshot_date, views_gained, imputed, corrected FROM video_daily ORDER BY 1").fetchall()
    assert rows[1][1:] == (100, False, False)
    assert rows[2][1:] == (150, True, False)       # 300 over a 2-day gap
    assert rows[3][1:] == (0, False, True)         # views fell: clipped to 0 and flagged


def test_coverage():
    con = store.connect(":memory:")
    build_world(con)
    for d in range(0, LAST + 1):   # UCf's lifetime views: its two tracked videos plus 3,000 a day of back catalog
        con.execute("INSERT INTO channel_snapshots VALUES (?, 'UCf', 1000, ?, 10)", [day(d), 1_000_000 + 5000 * d])
    metrics.build(con)
    formulas.build(con)
    cov, back = con.execute("SELECT coverage, back_catalog_gain FROM creator_coverage WHERE channel_id = 'UCf' AND d = ?",
                            [day(LAST)]).fetchone()
    assert cov == pytest.approx(2000 / 5000) and back == 3000
