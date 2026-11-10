"""The niche surface: reach by creator size and video age, the skew ranking, and the ask view."""
import datetime as dt
import json

from terminal import ask, demo, pipeline, site, store, surface


def _world():
    con = store.connect(":memory:")
    w = demo.build(con, end=dt.date(2026, 10, 18), n_youtube=24, n_tiktok=4, n_instagram=2)
    pipeline.run(con, "rebuild", today=w["end"], stages=[s for s in pipeline.JOBS["rebuild"] if s.name in ("tag", "compute", "later")])
    return con


def test_cells_and_skew(tmp_path):
    con = _world()
    cells = surface.cells(con)
    pooled = [c for c in cells if c["level"] == "all"]
    assert pooled and {c["size_i"] for c in pooled} <= set(range(len(surface.SIZES)))
    assert {c["age"] for c in pooled} <= {a for _, a in surface.AGES}
    for c in cells:   # thin cells stay blank instead of showing a number from a handful of videos
        if c["n"] < surface.MIN_N or c["creators"] < surface.MIN_CREATORS:
            assert c["reach"] is None
    niche = [c for c in cells if c["level"] == "niche"]
    assert all(c["size_i"] < len(surface.SIZES_NICHE) and c["age"] in {a for _, a in surface.AGES_NICHE} for c in niche)
    sk = surface.skew(con)
    ranked = [r for r in sk if r["skew"] is not None]
    assert [r["skew"] for r in ranked] == sorted((r["skew"] for r in ranked), reverse=True)
    if ranked:
        assert ranked[0]["skew_pctl"] == 100
    # the site writes both datasets, and the ask view answers the same question in chat
    meta = site.build(con, tmp_path, source="synthetic")
    assert {"surface", "skew"} <= set(meta["datasets"])
    assert json.loads((tmp_path / "surface.json").read_text())
    assert "ask_niche_skew" in ask.build(con)
    assert con.execute("SELECT count(*) FROM ask_niche_skew").fetchone()[0] == len(sk)


def test_empty_database_has_no_surface():
    con = store.connect(":memory:")
    assert surface.skew(con) == [] or all(r["skew"] is None for r in surface.skew(con))
    assert surface.cells(con) == []
