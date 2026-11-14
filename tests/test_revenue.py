"""Revenue estimates, catalog value and financing capacity."""
import datetime as dt
import json

import numpy as np
import pandas as pd

from terminal import ask, demo, pipeline, revenue, site, store


def test_rates_resolve_by_prefix_and_tubelab_wins():
    econ = revenue.economics()
    assert econ["08.25"]["rpm_low"] < econ["08.25"]["rpm_mid"] < econ["08.25"]["rpm_high"]
    deep = revenue.rates_for("08.25.050", econ)
    assert deep["rpm_mid"] == econ["08.25"]["rpm_mid"] and deep["rpm_source"] == "Published range"
    sector = revenue.rates_for("08", econ)
    assert sector["rpm_basis"].startswith("Median") and sector["rpm_low"] > 0
    tl = {"08.25.050": {"rpm_low": 9.0, "rpm_mid": 12.0, "rpm_high": 15.0, "channels": 30, "as_of": dt.date(2026, 11, 1)}}
    assert revenue.rates_for("08.25.050", econ, tl)["rpm_mid"] == 12.0
    assert revenue.rates_for("99.99", econ) is None


def test_decay_fit_recovers_the_exponent():
    rng = np.random.default_rng(1)
    rows = []
    for ch in range(10):
        for _ in range(30):
            age = rng.uniform(7, 900)
            rows.append((f"c{ch}", age, 1000 * (ch + 1) * age ** -1.3 * np.exp(rng.normal(0, 0.1))))
    k = revenue.fit_decay(pd.DataFrame(rows, columns=["channel_id", "age", "daily"]))
    assert abs(k - 1.3) < 0.05
    assert revenue.fit_decay(pd.DataFrame(rows[:10], columns=["channel_id", "age", "daily"])) is None


def test_projection_and_finance_math():
    m = revenue.project([(30, 100.0)], 0, 365, k=1.0)
    assert m[0] > m[1] > m[-1] > 0                                    # views fall every month
    flat = np.full(12, 1000.0)
    assert abs(revenue.pv(flat, 0.0) - 12000) < 1e-6 and revenue.pv(flat) < 12000
    assert abs(revenue.annuity_pv(100, 12, 1e-9) - 1200) < 1e-3
    assert revenue.half_life(np.array([10, 8, 5, 4])) == 3


def test_revenue_on_demo_world(tmp_path):
    con = store.connect(":memory:")
    w = demo.build(con, end=dt.date(2026, 10, 18), n_youtube=24, n_tiktok=4, n_instagram=2)
    pipeline.run(con, "rebuild", today=w["end"],
                 stages=[s for s in pipeline.JOBS["rebuild"] if s.name in ("tag", "business", "compute", "later", "revenue")])
    rows = con.execute("SELECT * FROM creator_revenue").df()
    assert len(rows) > 0
    for case in revenue.CASES:
        assert (rows[f"total_{case}"] >= rows[f"ad_{case}"] - 1e-9).all()
    assert (rows["catalog_low"] <= rows["catalog_mid"]).all() and (rows["catalog_mid"] <= rows["catalog_high"]).all()
    assert (rows["advance_capacity"] <= rows["catalog_low"]).all()
    assert rows["shorts_share"].between(0, 1).all()
    assert con.execute("SELECT count(*) FROM niche_decay").fetchone()[0] >= 1
    meta = site.build(con, tmp_path, source="synthetic")
    assert "revenue" in meta["datasets"]
    data = json.loads((tmp_path / "revenue.json").read_text())
    assert data and data[0]["catalog_mid"] >= data[-1]["catalog_mid"]
    assert "ask_creator_revenue" in ask.build(con)


def test_no_history():
    con = store.connect(":memory:")
    assert revenue.build(con) == "no history yet"
