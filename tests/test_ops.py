"""Usage tracking and the operations board."""
import datetime as dt
import json

from terminal import demo, ops, pipeline, site, store, usage


class _U:
    input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens = 1_000_000, 100_000, 0, 2_000_000


def test_usage_prices_claude_tokens_and_flushes_per_stage():
    con = store.connect(":memory:")
    usage.BUF.clear()
    usage.anthropic(type("M", (), {"usage": _U()})(), "claude-haiku-5-5", "classify videos")
    usage.anthropic(type("M", (), {"usage": _U()})(), "claude-haiku-5-5", "classify videos", batch=True)
    usage.youtube("search"); usage.youtube("videos"); usage.tubelab()
    assert usage.flush(con, "r1", "daily", "classify_videos") == 5 and not usage.BUF
    rows = dict(con.execute("SELECT service, sum(cost_usd) FROM api_usage GROUP BY 1").fetchall())
    # 1M input at $0.10 + 0.1M output at $0.50 + 2M cache reads at $0.01 = $0.17; the batch call is half
    assert abs(rows["Claude API"] - (0.17 + 0.085)) < 1e-9
    units = dict(con.execute("SELECT service, sum(amount) FROM api_usage GROUP BY 1").fetchall())
    assert units["YouTube Data API"] == 101 and units["TubeLab"] == 5


def test_ops_board_on_demo_world(tmp_path):
    con = store.connect(":memory:")
    w = demo.build(con, end=dt.date(2026, 10, 18), n_youtube=24, n_tiktok=4, n_instagram=2)
    pipeline.run(con, "daily", today=w["end"], stages=[s for s in pipeline.JOBS["rebuild"] if s.name in ("compute", "revenue")])
    d = ops.build(con, today=w["end"])
    assert {b["status"] for b in d["board"]} <= {"OK", "WARN", "FAIL", "OFF", "WAIT"}
    assert any(b["item"] == "Backups" and b["status"] == "OFF" for b in d["board"])
    assert d["runs"] and d["runs"][0]["job"] == "daily" and d["runs"][0]["ok"] == 2
    assert d["costs"]["fixed_total"] >= 39 and d["costs"]["limits"][0]["limit"] == 10000
    site.build(con, tmp_path / "data", source="live")
    assert json.loads((tmp_path / "api" / "ops.json").read_text())["board"]      # live: under /api/, owner-only
