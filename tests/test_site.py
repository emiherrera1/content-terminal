"""The site's data files: every dataset the page reads, in the shape it reads it."""
import datetime as dt
import json

from terminal import demo, pipeline, site, store

PAGE_FIELDS = {
    "niches": {"niche_code", "niche", "tag", "sector", "tve", "si", "ngp", "ccos", "chg", "trend", "risk", "volatility", "cr3"},
    "index_daily": {"day", "sector", "avg_ccos"},
    "screener": {"channel", "channel_id", "niche", "subscribers", "view_growth_28d", "growth_vs_niche", "uploads_28d", "outlier_rate"},
    "methodology": {"code", "name", "measures", "formula", "inputs", "read", "status", "section", "ref"},
    "creator_details": {"channel", "channel_id", "niche", "subscribers", "view_growth_28d", "growth_vs_niche", "outlier_rate", "cvs",
                        "top_video_share", "earnings_low_90d", "earnings_high_90d", "rpm_source"},
}


def test_site_files_match_the_page(tmp_path):
    con = store.connect(":memory:")
    w = demo.build(con, end=dt.date(2026, 10, 18), n_youtube=24, n_tiktok=4, n_instagram=2)
    pipeline.run(con, "rebuild", today=w["end"], stages=[s for s in pipeline.JOBS["rebuild"] if s.name in ("compute", "later")])
    meta = site.build(con, tmp_path, source="synthetic")
    assert meta["source"] == "synthetic" and meta["as_of"] == "2026-10-18"
    assert set(meta["datasets"]) >= {"niches", "index_daily", "screener", "alerts", "news", "methodology", "coverage", "formats_sheet"}
    # the demo fills every screen with made-up data, never real screening counts or real headlines
    news = json.loads((tmp_path / "news.json").read_text())
    assert news and all(n["source"] == "Demo Wire (synthetic)" and "example.com" in n["url"] for n in news)
    cov = json.loads((tmp_path / "coverage.json").read_text())
    assert cov and all(r["screened"] == r["kept"] + r["review"] + r["dropped"] for r in cov)
    assert json.loads((tmp_path / "formats_sheet.json").read_text())
    plats = {r["platform"] for r in json.loads((tmp_path / "platform_overview.json").read_text())}
    assert plats == {"YouTube", "TikTok", "Instagram"}
    assert json.loads((tmp_path / "platform_series.json").read_text())
    for name, fields in PAGE_FIELDS.items():
        rows = json.loads((tmp_path / f"{name}.json").read_text())
        assert rows, name
        assert fields <= set(rows[0]), (name, fields - set(rows[0]))


def test_formats_csv_parses(tmp_path, monkeypatch):
    f = tmp_path / "formats.csv"
    f.write_text("Formula,Change,Outlier,Triggers\nI tried X for 30 days,\"1,250%\",8.5x,\"Curiosity, Challenge\"\nEmpty,-,-,\n")
    monkeypatch.delenv("FORMATS_SHEET_CSV_URL", raising=False)
    monkeypatch.setattr(site, "FORMATS_CSV", f)
    rows = site.formats_sheet()
    assert rows[0] == {"formula": "I tried X for 30 days", "change_pct": 1250.0, "outlier": 8.5, "triggers": "Curiosity, Challenge"}
    assert rows[1]["change_pct"] is None and rows[1]["outlier"] is None
