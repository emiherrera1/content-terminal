"""The Later set, the statistics, the sources and the pipeline, run on a small synthetic world."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from terminal import analytics, demo, formulas, later, pipeline, sources, store


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    con = store.connect(":memory:")
    w = demo.build(con, end=dt.date(2026, 10, 18), n_youtube=24, n_tiktok=8, n_instagram=4)
    folder = tmp_path_factory.mktemp("calls")
    paths = demo.write_calls(con, w, folder)
    r = pipeline.run(con, "rebuild", today=w["end"], calls_paths=paths,
                     stages=[s for s in pipeline.JOBS["rebuild"] if s.name != "export"])
    assert r["ok"], r
    return con, w


def rows(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_every_table_is_built(world):
    con, _ = world
    for t in formulas.TABLES + later.TABLES + analytics.TABLES:
        con.execute(f"SELECT * FROM {t} LIMIT 1")
    for t in ["video_decay", "creator_flow", "creator_earnings", "entity_daily", "topic_purchase_intent", "platform_daily",
              "platform_z", "basket_coverage", "topic_ci", "lead_lag", "call_scores", "composite_index", "platform_lead",
              "packaging_wins", "creator_subs", "data_quality_daily", "audit_error_rate"]:
        assert rows(con, t) > 0, t


def test_decay_matches_the_generator(world):
    con, _ = world
    # the generator's decay rates run 0.08-0.35 a day, plus a flat tail, so fitted rates sit inside that band
    lo, hi = con.execute("SELECT quantile_cont(decay_rate, 0.1), quantile_cont(decay_rate, 0.9) FROM video_decay").fetchone()
    assert 0 < lo < hi < 0.4
    assert con.execute("SELECT min(half_life_days) > 0 FROM video_decay").fetchone()[0]


def test_earnings_are_a_labeled_range(world):
    con, _ = world
    lo, hi, src = con.execute("SELECT min(earnings_high_90d - earnings_low_90d), max(earnings_high_90d), any_value(rpm_source) "
                              "FROM creator_earnings").fetchone()
    assert lo >= 0 and hi > 0 and src == "placeholder"


def test_three_platforms_and_a_composite(world):
    con, _ = world
    assert {r[0] for r in con.execute("SELECT DISTINCT platform FROM platform_daily").fetchall()} == {"youtube", "tiktok", "instagram"}
    w = con.execute("SELECT sum(weight) FROM composite_index WHERE platform <> 'ALL' AND code = '03.07' "
                    "GROUP BY d ORDER BY d DESC LIMIT 1").fetchone()[0]
    assert w == pytest.approx(1.0)


def test_purchase_intent_is_higher_on_reviews(world):
    con, _ = world
    rev, other = con.execute("""SELECT avg(p.purchase_intent) FILTER (WHERE lower(v.title) LIKE '%review%' OR lower(v.title) LIKE '%worth it%'),
                                       avg(p.purchase_intent) FILTER (WHERE NOT (lower(v.title) LIKE '%review%' OR lower(v.title) LIKE '%worth it%'))
                                FROM purchase_intent p JOIN videos v USING (video_id)""").fetchone()
    assert rev > other


def test_calls_are_scored(world):
    con, _ = world
    s = con.execute("SELECT calls, resolved, hit_rate, brier FROM call_summary").fetchone()
    assert s[0] == 24 and s[1] > 0 and 0 <= s[2] <= 1 and 0 <= s[3] <= 1


def test_ci_brackets_the_estimate(world):
    con, _ = world
    bad = con.execute("SELECT count(*) FROM topic_ci WHERE NOT (wow_low <= wow + 1e-9 AND wow - 1e-9 <= wow_high)").fetchone()[0]
    assert bad == 0


# ---- the statistics on series with known answers
def _weekly(values, start="2025-01-06"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="7D"))


def test_lead_lag_finds_a_known_lead():
    rng = np.random.default_rng(1)
    walk = np.cumsum(rng.normal(0, 0.1, 80))
    x = _weekly(np.exp(walk[5:]))                      # x(t) = walk(t + 5)
    y = _weekly(np.exp(walk[:-5] + rng.normal(0, 0.01, 75)))    # y(t) = walk(t): five weeks behind x
    curve = analytics.lead_lag_curve(x, y, 12)
    assert max(curve, key=lambda r: r[1])[0] == 5


def test_granger_detects_a_real_lead_and_not_noise():
    rng = np.random.default_rng(2)
    n = 120
    x = rng.normal(0, 0.1, n)
    y = np.zeros(n)
    for t in range(1, n):
        y[t] = 0.8 * x[t - 1] + rng.normal(0, 0.02)
    lead = analytics.granger(_weekly(np.exp(np.cumsum(x))), _weekly(np.exp(np.cumsum(y))), 2, 0.25)
    noise = analytics.granger(_weekly(np.exp(np.cumsum(rng.normal(0, 0.1, n)))), _weekly(np.exp(np.cumsum(y))), 2, 0.25)
    assert lead["p_value"] < 0.01 and lead["out_of_sample_better"]
    assert noise["p_value"] > 0.01


def test_judge_hit_and_miss():
    flat = [100.0 * (1.02 if i % 2 else 0.98) for i in range(40)]         # typical 13-week move is small
    up = _weekly(flat + [130.0] * 5)
    t0 = up.index[39]
    assert analytics._judge(up, t0, 4, "up", 13)["status"] == "hit"
    assert analytics._judge(up, t0, 4, "down", 13)["status"] == "miss"


# ---- sources and the pipeline
def test_unconfigured_sources_are_skipped(monkeypatch, tmp_path):
    con = store.connect(":memory:")
    for k in ("TIKTOK_CLIENT_KEY", "INSTAGRAM_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(sources.NotConfigured):
        sources.pull_comments(con)
    with pytest.raises(sources.NotConfigured):
        sources.load_outcome_csvs(con, tmp_path)
    for a in sources.PLATFORMS:
        with pytest.raises(sources.NotConfigured):
            a.pull(con, dt.date.today())


def test_outcome_csv_loads(tmp_path):
    con = store.connect(":memory:")
    (tmp_path / "x.csv").write_text("series_id,code,label,d,value\ns1,03.07,Test,2026-10-14,5\n")
    assert sources.load_outcome_csvs(con, tmp_path) == 1
    assert con.execute("SELECT d FROM outcome_series").fetchone()[0] == dt.date(2026, 10, 12)   # stored by week start


def test_pipeline_retries_skips_and_stops_on_critical():
    con = store.connect(":memory:")
    calls = {"n": 0}

    def flaky(ctx):
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("blip")
        return "ok after retry"

    def missing(ctx):
        raise sources.NotConfigured("no key")

    def broken(ctx):
        raise ValueError("bad")

    after = {"ran": False}

    def later_stage(ctx):
        after["ran"] = True

    stages = [pipeline.Stage("flaky", flaky, retries=2), pipeline.Stage("missing", missing),
              pipeline.Stage("optional", broken), pipeline.Stage("core", broken, critical=True),
              pipeline.Stage("after", later_stage)]
    r = pipeline.run(con, "test", stages=stages, sleep=lambda s: None)
    status = {k: v[0] for k, v in r["results"].items()}
    assert status == {"flaky": "ok", "missing": "skipped", "optional": "failed", "core": "failed"}
    assert not r["ok"] and r["failed"] == ["core"] and not after["ran"]
    assert con.execute("SELECT count(*) FROM pipeline_runs").fetchone()[0] == 4


def test_health_flags_stale_data():
    con = store.connect(":memory:")
    con.execute("INSERT INTO channel_snapshots VALUES ('2026-10-10', 'UCx', 1, 1, 1)")
    ok, lines = pipeline.health(con, today=dt.date(2026, 10, 19))
    assert not ok and lines[0].startswith("STALE")
