"""Formula Library items moved from Next to Built: 1.8, 1.11, 3.8, 3.11, 6.5, 10.1-10.3."""
import datetime as dt
import statistics

import pytest

from terminal import classify, formulas, metrics, store
from tests.test_formulas import LAST, WOOD, build_world, day, topic_gain


def built(cfg=None):
    con = store.connect(":memory:")
    build_world(con)
    metrics.build(con)
    if cfg:
        formulas.build(con, cfg)
    return con


def test_ewma_matches_the_recursion():
    con = built()
    alpha = formulas.config()["windows"]["ewma_alpha"]
    rows = con.execute("SELECT d, a, ewma FROM topic_series WHERE code = ? ORDER BY d", [WOOD]).fetchall()
    e = None
    for _, a, got in rows:
        e = a if e is None else alpha * a + (1 - alpha) * e
        assert got == pytest.approx(e)


def test_advance_decline():
    con = built()
    adv, dec, ad = con.execute("SELECT advancers, decliners, advance_decline FROM topic_current WHERE code = ?",
                               [WOOD]).fetchone()
    # UCa's new review video lifts its last 7 days; UCb and UCn run at a constant rate
    assert (adv, dec, ad) == (1, 0, 1)


def test_confident_share():
    con = store.connect(":memory:")
    build_world(con)
    con.execute("UPDATE video_classifications SET confidence = 0.5 WHERE video_id = 'n1'")
    metrics.build(con)
    got = con.execute("SELECT confident_share FROM topic_series WHERE code = ? AND d = ?", [WOOD, day(LAST)]).fetchone()[0]
    total = topic_gain(LAST, WOOD)
    assert got == pytest.approx((total - topic_gain(LAST, WOOD, {"UCn"})) / total)


def test_splice_factor():
    cfg = formulas.config()
    b = day(30)
    cfg["breaks"] = [b.isoformat()]
    con = built(cfg)
    before = statistics.median(topic_gain(d, WOOD) for d in range(16, 30))
    after = statistics.median(topic_gain(d, WOOD) for d in range(30, 44))
    got = con.execute("SELECT before_median, after_median, splice_factor FROM topic_breaks WHERE code = ?", [WOOD]).fetchone()
    assert got == pytest.approx((before, after, after / before))


def test_subscriber_rounding():
    con = store.connect(":memory:")
    subs = [123000, 123000, 123000, 124000, 124000, 124000, 126000, 126000]
    for i, s in enumerate(subs):
        con.execute("INSERT INTO channel_snapshots VALUES (?, 'UCx', ?, 0, 0)", [day(i), s])
    metrics.build(con)
    rows = con.execute("SELECT rounding_step, subs_change_reported, subs_gained_est FROM creator_subs ORDER BY d").fetchall()
    assert {r[0] for r in rows} == {1000}
    assert [r[1] for r in rows] == [None, None, None, 1000, None, None, 2000, None]
    est = [r[2] for r in rows]
    assert est[0] is None and est[7] is None                     # before the first step and after the latest
    assert est[1:4] == pytest.approx([1000 / 3] * 3) and est[4:7] == pytest.approx([2000 / 3] * 3)


def test_pull_completeness():
    con = built()
    for ch in ("UCa", "UCb", "UCf"):                              # UCn missed on the last day
        con.execute("INSERT INTO channel_snapshots VALUES (?, ?, 1000, 0, 0)", [day(LAST), ch])
    metrics.build(con)
    exp, rec, comp = con.execute("SELECT channels_expected, channels_received, pull_completeness "
                                 "FROM data_quality_daily WHERE d = ?", [day(LAST)]).fetchone()
    assert (exp, rec, comp) == (4, 3, 0.75)


def test_audit_error_rate(tmp_path):
    con = built()
    path = classify.audit_sheet(con, "video", n=5, out_dir=str(tmp_path))
    lines = path and open(path).read().splitlines()
    header, rows = lines[0], lines[1:]
    assert len(rows) == 5 and header.endswith("correct_code")
    # mark one of the five wrong
    first = rows[0].split(",")
    rows[0] = ",".join(first[:-1] + ["03.08.015"])
    open(path, "w").write("\n".join([header] + rows) + "\n")
    r = classify.record_audit(con, path, "emily", day(LAST))
    assert (r["checked"], r["wrong"]) == (5, 1)
    metrics.build(con)
    got = con.execute("SELECT sum(checked), sum(wrong) FROM audit_error_rate WHERE kind = 'video'").fetchone()
    assert got == (5, 1)
    assert con.execute("SELECT count(*) FROM video_classifications WHERE reviewed").fetchone()[0] == 5
    assert con.execute("SELECT topic_code FROM video_classifications WHERE video_id = ?", [first[1]]).fetchone()[0] == "03.08.015"


def test_crosswalk_loads():
    con = built()
    rows = con.execute("SELECT code, niche, status FROM topic_crosswalk ORDER BY code").fetchall()
    assert len(rows) == 8 and rows[0][:2] == ("03.07", "Fitness") and {r[2] for r in rows} == {"draft"}
