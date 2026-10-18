"""Creator metrics, the Creator Viability Score, tiers, and the tamper-evident call log."""
import csv
import datetime as dt
import math

import pytest

from terminal import calls, creators, formulas, metrics, store
from tests.test_formulas import LAST, VIDEOS, WOOD, build_world, day


@pytest.fixture
def con():
    con = store.connect(":memory:")
    build_world(con)
    # channel snapshots: UCa large and growing, UCn small
    for d in range(LAST + 1):
        con.execute("INSERT INTO channel_snapshots VALUES (?, 'UCa', 120000, ?, 7)", [day(d), 5_000_000 + 30_000 * d])
        con.execute("INSERT INTO channel_snapshots VALUES (?, 'UCb', 40000, ?, 4)", [day(d), 2_000_000 + 2_000 * d])
        con.execute("INSERT INTO channel_snapshots VALUES (?, 'UCn', 3000, ?, 1)", [day(d), 10_000 + 2_000 * d])
        con.execute("INSERT INTO channel_snapshots VALUES (?, 'UCf', 60000, ?, 2)", [day(d), 3_000_000 + 2_000 * d])
    metrics.build(con)
    return con


def gini(xs):
    xs = sorted(xs)
    n = len(xs)
    return 2 * sum((i + 1) * x for i, x in enumerate(xs)) / (n * sum(xs)) - (n + 1) / n


def test_creator_metrics_match_independent_math(con):
    m = con.execute("SELECT top_video_share, gini, uploads_per_week, regularity, views_28d, focus, niche "
                    "FROM creator_metrics WHERE channel_id = 'UCa'").fetchone()
    top, g, upw, reg, v28, focus, niche = m
    # 90-day gains per UCa video (all of the data window is inside 90 days)
    gains = {v: sum(r for d in range(LAST + 1) if (r := (VIDEOS[v][4] if d > max(VIDEOS[v][3], 0) else 0)))
             for v in VIDEOS if VIDEOS[v][0] == "UCa"}
    assert top == pytest.approx(max(gains.values()) / sum(gains.values()))
    current = [VIDEOS[v][4] * (LAST - VIDEOS[v][3]) for v in gains]
    assert g == pytest.approx(gini(current))
    pubs = sorted(VIDEOS[v][3] for v in gains)   # days 5..30 and 54: all within 90 days
    assert upw == pytest.approx(len(pubs) / (90 / 7))
    gaps = [b - a for a, b in zip(pubs, pubs[1:])]
    mean = sum(gaps) / len(gaps)
    sd = math.sqrt(sum((x - mean) ** 2 for x in gaps) / (len(gaps) - 1))
    assert reg == pytest.approx(max(0, 1 - sd / mean))
    assert v28 == 30_000 * 28
    assert focus == 1.0 and niche == "02.05.010"   # one sub-niche only


def test_cvs_pillars_and_gates(con):
    rows = {r[0]: r[1:] for r in con.execute(
        "SELECT channel_id, cvs, momentum + consistency_pillar + audience_depth + market_opportunity + hit_dependence, "
        "auto_gates, manual_gates FROM creator_cvs").fetchall()}
    for cid, (cvs, total, auto, manual) in rows.items():
        assert 0 <= cvs <= 100 and cvs == pytest.approx(total)
    assert rows["UCa"][2] is True          # 120K subs, 840K views in 28 days, uploaded in the last 30 days
    assert rows["UCn"][2] is False         # 3K subscribers
    assert rows["UCb"][2] is False         # no upload in the last 30 days
    assert not any(r[3] for r in rows.values())   # nobody reviewed yet


def tier_world(history, today_cvs, manual=False):
    con = store.connect(":memory:")
    cfg = formulas.config()
    con.execute("CREATE TABLE creator_cvs AS SELECT 'UCx' AS channel_id, 'X' AS title, '03.07.014' AS niche, "
                "?::DOUBLE AS cvs, 0.0 AS momentum, 0.0 AS consistency_pillar, 0.0 AS audience_depth, "
                "0.0 AS market_opportunity, 0.0 AS hit_dependence, true AS auto_gates, ? AS manual_gates", [today_cvs, manual])
    con.execute("CREATE TABLE cvs_history (d DATE, channel_id VARCHAR, cvs DOUBLE, tier VARCHAR)")
    start = dt.date(2026, 11, 1)
    for i, (score, tier) in enumerate(history):
        con.execute("INSERT INTO cvs_history VALUES (?, 'UCx', ?, ?)", [start + dt.timedelta(days=i), score, tier])
    con.execute("INSERT INTO cvs_history VALUES (?, 'UCx', ?, NULL)", [start + dt.timedelta(days=len(history)), today_cvs])
    con.execute(creators.watchlist_sql(cfg))
    con.execute(creators.ALERTS)
    return con


def test_tiers_need_the_score_to_hold():
    con = tier_world([(80, "Watch")] * 12, 80)
    assert con.execute("SELECT tier FROM creator_watchlist").fetchone()[0] == "Watch"     # 13 days: not 14 yet
    con = tier_world([(80, "Watch")] * 13, 80)
    assert con.execute("SELECT tier FROM creator_watchlist").fetchone()[0] == "Priority"  # 14 days at 75+
    alert = con.execute("SELECT prev_tier, tier FROM creator_alerts").fetchone()
    assert alert == ("Watch", "Priority")
    con = tier_world([(90, "Priority")] * 27, 90)
    w = con.execute("SELECT tier, needs_review FROM creator_watchlist").fetchone()
    assert w == ("Priority", True)        # 28 days at 85+, but the manual gates aren't confirmed
    con = tier_world([(90, "Priority")] * 27, 90, manual=True)
    assert con.execute("SELECT tier FROM creator_watchlist").fetchone()[0] == "Diligence-ready"
    con = tier_world([(80, "Priority")] * 20, 50)
    assert con.execute("SELECT tier FROM creator_watchlist").fetchone()[0] is None        # dropped below Watch


def test_call_log_is_append_only_and_tamper_evident(tmp_path, con):
    cp, rp = tmp_path / "calls.csv", tmp_path / "res.csv"
    c1 = calls.add(con, "topic", WOOD, "dss", "up", 13, "Google Trends: woodworking, US", 0.6, "Emily", "test", "Woodworking", cp)
    c2 = calls.add(con, "creator", "UCa", "cvs", "up", 26, "Brand deal announced", None, "Emily", "", "UCa", cp)
    assert c1["id"] == 1 and c2["id"] == 2 and c2["prev_hash"] == c1["hash"]
    assert c1["score"] == pytest.approx(con.execute(
        "SELECT dss FROM topic_series WHERE code = ? ORDER BY d DESC LIMIT 1", [WOOD]).fetchone()[0], abs=1e-4)
    calls.resolve(1, "hit", "search interest up 18%", "Emily", cp, rp)
    with pytest.raises(ValueError):
        calls.resolve(1, "miss", "", "", cp, rp)          # already resolved
    assert calls.verify(cp, calls.CALL_FIELDS) == [] and calls.verify(rp, calls.RES_FIELDS) == []
    assert [c["outcome"] for c in calls.table(cp, rp)] == ["hit", "open"]
    # edit an earlier call after the fact: the chain catches it
    rows = list(csv.DictReader(open(cp)))
    rows[0]["direction"] = "down"
    with open(cp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=calls.CALL_FIELDS)
        w.writeheader()
        w.writerows(rows)
    assert calls.verify(cp, calls.CALL_FIELDS) == ["row 1: contents changed after it was written"]
