"""Creator companies: the sheet's Feed tabs into the database, the Companies screens and the ask views."""
import datetime as dt
import json

from terminal import ask, companies, demo, pipeline, site, store

# shaped like the published Feed tabs: formula outputs, thousands separators, US dates
CREATORS = """Creator,Channel ID,In terminal,Stage,Stage name,Main company,Outside money,"Disclosed raised ($M, USD)",Research status,Confidence,Source,Notes,Last checked
MrBeast,,No (reference),5,Outside capital,Beast Industries,Yes,200.0,Done,Reported,https://example.com/a ; https://example.com/b,Holdco.,2026-11-06
Chef Ann,,Yes,,,,,,To research,,,,
Big Studio,UCbig,Yes,4,Holding company,Big Studio Group,None found,,Done,Confirmed,https://example.com/c,,11/6/2026
Odd Stage,UCodd,Yes,9,,,,,Done,Inferred,,,
"""
COMPANIES = """Company,Creator,Relationship,Type,Parent company,What it does,Status,Source,Confidence,Last checked
Beast Industries,MrBeast,Owns,Holding,,Holding company,Active,https://example.com/a,Reported,2026-11-06
Feastables,MrBeast,Owns,Brand,Beast Industries,Chocolate,Active,https://example.com/a,Reported,2026-11-06
"""
ROUNDS = """Date,Company,Creator,Round,Amount ($M),Valuation ($M),Currency,Lead investors,Other investors,Source,Confidence,Last checked
2026-01-15,Beast Industries,MrBeast,Equity,200.0,"5,000.0",usd,Example Ventures; Sample Partners,,https://example.com/a,Reported,2026-11-06
,Beast Industries,MrBeast,Earlier round,,,USD,Placeholder Capital,,https://example.com/b,Reported,2026-11-06
"""


def _local(tmp_path, monkeypatch):
    monkeypatch.delenv("COMPANIES_SHEET_CSV_URL", raising=False)
    monkeypatch.setattr(companies, "LOCAL", tmp_path)
    for tab, text in (("creators", CREATORS), ("companies", COMPANIES), ("rounds", ROUNDS)):
        (tmp_path / f"companies_{tab}.csv").write_text(text)


def test_parse_and_load(tmp_path, monkeypatch):
    _local(tmp_path, monkeypatch)
    con = store.connect(":memory:")
    con.execute("INSERT INTO channels (channel_id, title) VALUES ('UCann', 'Chef Ann')")
    msg = companies.load(con)
    assert msg == "4 creators (3 researched), 2 companies, 2 rounds"
    rows = {r[0]: r[1:] for r in con.execute("SELECT creator, channel_id, stage, stage_name, research_status, last_checked FROM creator_companies").fetchall()}
    assert rows["Chef Ann"] == ("UCann", None, None, "To research", None)       # id filled from channels; blank stage = not researched
    assert rows["Big Studio"][1:3] == (4, "Holding company") and rows["Big Studio"][4] == dt.date(2026, 11, 6)
    assert rows["Odd Stage"][1] is None                                          # stages outside 1-6 are dropped
    r = con.execute("SELECT amount_m, valuation_m, currency FROM creator_rounds WHERE round_date IS NOT NULL").fetchone()
    assert r == (200.0, 5000.0, "USD")
    # the sheet is the record: a second load replaces, never duplicates
    companies.load(con)
    assert con.execute("SELECT count(*) FROM creator_rounds").fetchone()[0] == 2


def test_feed_url_picks_each_tab():
    base = "https://docs.google.com/spreadsheets/d/e/2PACX-abc/pub?gid=0&single=true&output=csv"
    u = companies._feed_url(base, 11)
    assert u.startswith("https://docs.google.com/spreadsheets/d/e/2PACX-abc/pub?") and "gid=11" in u and "gid=0" not in u and "output=csv" in u


def test_companies_screens_and_ask_views(tmp_path, monkeypatch):
    _local(tmp_path, monkeypatch)
    con = store.connect(":memory:")
    w = demo.build(con, end=dt.date(2026, 10, 18), n_youtube=24, n_tiktok=4, n_instagram=2)
    pipeline.run(con, "rebuild", today=w["end"], stages=[s for s in pipeline.JOBS["rebuild"] if s.name in ("tag", "compute", "later")])
    # point one sheet row at a tracked creator so the merge with the terminal's numbers is exercised
    cid, title = con.execute("SELECT channel_id, title FROM screener LIMIT 1").fetchone()
    (tmp_path / "companies_creators.csv").write_text(CREATORS.replace("Big Studio,UCbig", f"{title},{cid}"))
    companies.load(con)
    out = tmp_path / "live"
    meta = site.build(con, out)
    assert {"corp", "corp_companies", "corp_rounds"} <= set(meta["datasets"])
    corp = {r["creator"]: r for r in json.loads((out / "corp.json").read_text())}
    assert corp[title]["tracked"] and corp[title]["subscribers"] is not None and corp[title]["stage"] == 4
    assert corp["MrBeast"]["tracked"] is False and corp["MrBeast"]["companies"] == 2 and corp["MrBeast"]["last_round"] == "2026-01-15"
    made = ask.build(con)
    assert {"ask_creator_companies", "ask_creator_rounds"} <= set(made)
    assert con.execute("SELECT count(*) FROM ask_creator_rounds").fetchone()[0] == 2
    # the demo shows made-up companies for made-up creators, never the real sheet
    demo_out = tmp_path / "demo"
    site.build(con, demo_out, source="synthetic")
    dc = json.loads((demo_out / "corp.json").read_text())
    assert dc and all("SYNTHETIC" in r["notes"] for r in dc) and not any(r["creator"] == "MrBeast" for r in dc)
    names = {i.strip() for r in json.loads((demo_out / "corp_rounds.json").read_text()) for i in (r["lead_investors"] + ";" + r["other_investors"]).split(";")}
    assert names and all(n in {"Example Ventures", "Sample Growth Partners", "Placeholder Capital", "Demo Strategic Fund", "Mock Media Holdings"} for n in names)
