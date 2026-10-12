"""Metric views on synthetic data: no API keys needed."""
import datetime as dt

import pytest

from terminal import ctncs, metrics, store

TODAY = dt.date(2026, 10, 12)


@pytest.fixture
def con():
    con = store.connect(":memory:")
    # Two cooking channels in one niche, one gaming channel, one unclassified.
    channels = [("UCa", "Steady Cooks", "03.01.01"), ("UCb", "Spiky Cooks", "03.01.01"),
                ("UCc", "Game Lab", "07.01.01"), ("UCd", "Mystery", "unclassified")]
    for cid, title, code in channels:
        con.execute("INSERT INTO channels (channel_id, title, uploads_playlist) VALUES (?, ?, ?)", [cid, title, "UU" + cid])
        parts = code.split(".") if code != "unclassified" else []
        con.execute("INSERT INTO classifications (channel_id, ctncs_code, sector, category, niche, confidence) "
                    "VALUES (?, ?, ?, ?, ?, 0.9)",
                    [cid, code, parts[0] if parts else None, ".".join(parts[:2]) or None, ".".join(parts[:3]) or None])
    a = b = 100_000
    for i in range(40):
        day = TODAY - dt.timedelta(days=39 - i)
        gain = 1000 + (i % 3) * 10  # ~1,000 a day with a little noise
        if i:
            a += gain
            b += gain + (20_000 if i == 39 else 0)  # UCb spikes on the last day
        con.execute("INSERT INTO channel_snapshots VALUES (?, 'UCa', 1000, ?, 50)", [day, a])
        con.execute("INSERT INTO channel_snapshots VALUES (?, 'UCb', 2000, ?, 80)", [day, b])
        con.execute("INSERT INTO channel_snapshots VALUES (?, 'UCc', 500, ?, 30)", [day, 50_000 + i * 500])
        con.execute("INSERT INTO channel_snapshots VALUES (?, 'UCd', 100, ?, 10)", [day, 10_000 + i * 100])
    # UCb: one video far above its median.
    for j, views in enumerate([100, 120, 110, 900]):
        con.execute("INSERT INTO videos VALUES (?, 'UCb', 't', ?, 60)", [f"v{j}", dt.datetime(2026, 10, 1 + j)])
        con.execute("INSERT INTO video_snapshots VALUES (?, ?, ?, 0, 0)", [TODAY, f"v{j}", views])
    metrics.build(con)
    return con


def test_niche_index_sums_daily_gains(con):
    rows = dict(con.execute(
        "SELECT niche_code, views_gained FROM niche_daily WHERE snapshot_date = ?", [TODAY]).fetchall())
    assert rows["07.01.01"] == 500
    assert rows["03.01.01"] == 1000 + 1000 + 20_000  # i=39: no noise that day
    assert len(rows) == 2  # unclassified channel left out


def test_momentum_compares_weeks(con):
    m = dict(con.execute("SELECT niche_code, momentum FROM niche_momentum").fetchall())
    assert m["07.01.01"] == pytest.approx(0, abs=1e-9)  # flat growth
    assert m["03.01.01"] > 0  # the spike lifts the last week


def test_screener_ranks_against_niche(con):
    s = {r[0]: r for r in con.execute(
        "SELECT channel_id, growth_vs_niche, outlier_rate, niche_code FROM screener").fetchall()}
    assert set(s) == {"UCa", "UCb", "UCc", "UCd"}
    assert s["UCb"][1] > s["UCa"][1]
    assert s["UCb"][2] == pytest.approx(0.25)  # 1 of 4 videos > 3x median
    assert s["UCd"][3] is None


def test_alerts_flag_only_the_spike(con):
    flagged = [r[0] for r in con.execute("SELECT channel_id FROM alerts").fetchall()]
    assert flagged == ["UCb"]


def test_codebook_subtree():
    book = ctncs.load()
    rows = ctncs.subtree(book, ["03"])
    assert rows and all(r["code"].startswith("03") and int(r["level"]) <= 3 for r in rows)


def test_opportunity_metrics(con):
    rows = {r[0]: r for r in con.execute(
        "SELECT niche_code, si, ngp, ccos, trend, risk, tve FROM niche_opportunity").fetchall()}
    assert set(rows) == {"03.01.01", "07.01.01"}  # unclassified channel left out
    for code, si, ngp, ccos, trend, risk, tve in rows.values():
        assert 0 <= si < 1  # a niche with no tracked uploads has no saturation
        assert ccos == pytest.approx(20 * ngp * (1 - si))
        assert trend in {"UP", "DOWN", "STABLE"} and risk in {"LOW", "MEDIUM", "HIGH"}
    # cooking had a spike in the last week, gaming was flat
    assert rows["03.01.01"][2] > rows["07.01.01"][2] == pytest.approx(1.0)
    assert rows["03.01.01"][6] > 0 and rows["07.01.01"][6] == 0  # only cooking has tracked uploads
