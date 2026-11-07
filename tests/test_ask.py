"""The plain-English views for asking questions in chat."""
import datetime as dt

from terminal import ask, demo, news, pipeline, store


def test_ask_views_use_names_and_carry_definitions():
    con = store.connect(":memory:")
    news.connect(con)
    w = demo.build(con, end=dt.date(2026, 10, 18), n_youtube=24, n_tiktok=4, n_instagram=2)
    pipeline.run(con, "rebuild", today=w["end"], stages=[s for s in pipeline.JOBS["rebuild"] if s.name in ("compute", "later", "ask")])
    made = {v for (v,) in con.execute("SELECT view_name FROM ask_guide").fetchall()}
    assert {"ask_creators", "ask_niches", "ask_niche_history", "ask_creator_history", "ask_topic_attention", "ask_glossary", "ask_topics"} <= made
    # names, not codes
    assert con.execute("SELECT count(*) FROM ask_creators WHERE niche IS NULL OR sector IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM ask_niches WHERE niche IS NULL").fetchone()[0] == 0
    # one creator per row, matching the screener
    assert con.execute("SELECT count(*), count(DISTINCT channel_id) FROM ask_creators").fetchone() == \
        (con.execute("SELECT count(*) FROM screener").fetchone()[0],) * 2
    # definitions travel with the data
    assert "Creator Viability Score" in con.execute("SELECT comment FROM duckdb_columns() WHERE table_name = 'ask_creators' AND column_name = 'cvs'").fetchone()[0]
    assert con.execute("SELECT comment FROM duckdb_views() WHERE view_name = 'ask_niches'").fetchone()[0]
    # attention change is the growth ratio minus one
    assert con.execute("SELECT max(abs(attention_change_7d - (growth_7d_ratio - 1))) FROM ask_niches").fetchone()[0] < 1e-12
