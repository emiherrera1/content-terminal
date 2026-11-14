"""Long-form against Shorts by niche and creator, and the format-filtered surface."""
import datetime as dt

from terminal import ask, demo, formats, pipeline, store, surface


def test_format_split_on_demo_world():
    con = store.connect(":memory:")
    w = demo.build(con, end=dt.date(2026, 10, 18), n_youtube=24, n_tiktok=4, n_instagram=2)
    pipeline.run(con, "rebuild", today=w["end"], stages=[s for s in pipeline.JOBS["rebuild"] if s.name in ("compute", "formats")])
    rows = con.execute("SELECT niche_code, fmt, share, growth_7d FROM format_niche").fetchall()
    assert {r[1] for r in rows} == {"LONG", "SHORTS"}
    by = {}
    for code, _, share, _ in rows:
        by[code] = by.get(code, 0) + (share or 0)
    assert all(abs(v - 1) < 1e-9 for v in by.values())               # the two formats' shares add to the niche
    assert any(g is not None for *_, g in rows)                        # 180 days of history, so growth is filled
    c = con.execute("SELECT count(DISTINCT channel_id), max(top_video_share) FROM format_creator").fetchone()
    assert c[0] > 0 and 0 < c[1] <= 1
    long_n = sum(r["n"] for r in surface.cells(con, fmt="LONG") if r["level"] == "all")
    short_n = sum(r["n"] for r in surface.cells(con, fmt="SHORTS") if r["level"] == "all")
    all_n = sum(r["n"] for r in surface.cells(con) if r["level"] == "all")
    assert long_n + short_n == all_n
    assert {"ask_format_niches", "ask_format_creators"} <= set(ask.build(con))


def test_no_history():
    assert formats.build(store.connect(":memory:")) == "no history yet"
