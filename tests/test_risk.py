"""Creator risk stats on the synthetic world: volatility, beta, drawdown, growth per unit of risk."""
import datetime as dt
import json

from terminal import ask, demo, pipeline, risk, site, store


def test_risk_stats_on_demo_world(tmp_path):
    con = store.connect(":memory:")
    w = demo.build(con, end=dt.date(2026, 10, 18), n_youtube=24, n_tiktok=4, n_instagram=2)
    pipeline.run(con, "rebuild", today=w["end"], stages=[s for s in pipeline.JOBS["rebuild"] if s.name in ("tag", "business", "compute", "later", "risk")])
    rows = con.execute("SELECT volatility, beta, max_drawdown, growth, growth_per_risk, days FROM creator_risk").fetchall()
    assert rows, "the demo world has 180 days, so every creator gets stats"
    for vol, beta, dd, g, gpr, days in rows:
        assert vol > 0 and 0 <= dd <= 1 and days >= risk.MIN_RETURNS
        assert gpr is None or abs(gpr - g / vol) < 1e-9
    assert any(b is not None for _, b, *_ in rows)                 # niches with 2+ creators get a beta
    # site and chat
    meta = site.build(con, tmp_path, source="synthetic")
    assert {"risk", "business", "sponsors"} <= set(meta["datasets"])
    assert json.loads((tmp_path / "risk.json").read_text())
    biz = json.loads((tmp_path / "business.json").read_text())
    assert biz and any("Sponsorships" in r["streams"] for r in biz)
    assert {"ask_creator_risk", "ask_creator_business", "ask_sponsors"} <= set(ask.build(con))


def test_too_little_history_gives_no_row():
    con = store.connect(":memory:")
    assert risk.build(con) == "no history yet"
