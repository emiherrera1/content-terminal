"""Write the data files for the password-protected terminal site (site/public/data/*.json).

    python -m terminal site            from the database (TERMINAL_DB), for the daily deploy
    python -m terminal site --demo     from the synthetic world, labeled SYNTHETIC on the page

The files are built in the job and uploaded straight to Cloudflare Pages; they are never committed to git.
The page (site/public/index.html) reads data/manifest.json, then one JSON file per dataset.
"""
import csv
import datetime as dt
import io
import json
import os
import urllib.request
from pathlib import Path

from . import coverage

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "public" / "data"
FORMATS_CSV = ROOT / "data" / "inputs" / "formats.csv"


def _rows(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _has(con, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0])


def _clean(v):
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _labels():
    from . import ctncs
    try:
        book = ctncs.load()
    except Exception:
        book = {}
    sector = lambda code: (book.get((code or "").split(".")[0]) or {}).get("label", "Unclassified")
    return coverage._label, sector


def niches(con, label, sector) -> list[dict]:
    if not _has(con, "niche_opportunity"):
        return []
    return [{"niche_code": r["niche_code"], "niche": label(r["niche_code"]), "tag": r["niche_code"], "sector": sector(r["niche_code"]),
             "tve": r["tve"], "si": r["si"], "ngp": r["ngp"], "ccos": r["ccos"], "chg": r["ccos_chg_7d"], "trend": r["trend"],
             "risk": r["risk"], "volatility": r["volatility"], "cr3": r["cr3"]}
            for r in _rows(con, "SELECT * FROM niche_opportunity WHERE ccos IS NOT NULL")]


def index_daily(con, sector) -> list[dict]:
    if not _has(con, "niche_opportunity_daily"):
        return []
    acc = {}
    for r in _rows(con, "SELECT snapshot_date, niche_code, ccos FROM niche_opportunity_daily WHERE ccos IS NOT NULL "
                        "AND snapshot_date > (SELECT max(snapshot_date) FROM niche_opportunity_daily) - 90"):
        k = (r["snapshot_date"].isoformat(), sector(r["niche_code"]))
        acc.setdefault(k, []).append(r["ccos"])
    return [{"day": d, "sector": s, "avg_ccos": sum(v) / len(v)} for (d, s), v in sorted(acc.items())]


def screener(con, label) -> list[dict]:
    if not _has(con, "screener"):
        return []
    return [{"channel": r["title"], "channel_id": r["channel_id"], "niche": label(r["niche_code"]) if r["niche_code"] else "—",
             "subscribers": r["subscribers"], "view_growth_28d": r["view_growth_28d"], "growth_vs_niche": r["growth_vs_niche"],
             "uploads_28d": r["uploads_28d"], "outlier_rate": r["outlier_rate"]}
            for r in _rows(con, "SELECT * FROM screener")]


def alerts(con, label) -> list[dict]:
    if not _has(con, "alerts"):
        return []
    return [{"channel": r["title"], "channel_id": r["channel_id"], "niche": label(r["niche_code"]) if r["niche_code"] else "—",
             "views_today": r["views_gained"], "typical_daily_views": r["typical_daily_views"], "z_score": r["z_score"]}
            for r in _rows(con, "SELECT * FROM alerts ORDER BY z_score DESC")]


def news(con) -> list[dict]:
    if not _has(con, "news_ranked"):
        return []
    out = []
    for r in _rows(con, "SELECT * FROM news_ranked WHERE category IS NOT NULL ORDER BY published_at DESC LIMIT 80"):
        p = r.get("published_at")
        out.append({"url": r["url"], "title": r["title"], "source": r.get("source") or "",
                    "published": p.strftime("%Y-%m-%d %H:%M") if p else "", "category": r.get("category") or "",
                    "platform": r.get("platform") or "", "impact": r.get("impact") or "", "sentiment": r.get("sentiment") or "",
                    "creators": r.get("creators") or "", "score": r.get("score")})
    return out


def methodology() -> list[dict]:
    with open(ROOT / "config" / "methodology.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def formats_sheet() -> list[dict]:
    """The Trending Formats sheet. FORMATS_SHEET_CSV_URL (a GitHub secret) is the sheet's File > Share > Publish to web > CSV link
    for the trending_titles_master_clean tab; data/inputs/formats.csv is the fallback. Columns A-D: formula, change, outlier, triggers."""
    text = None
    url = os.environ.get("FORMATS_SHEET_CSV_URL")
    if url:
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                text = r.read().decode("utf-8")
        except OSError as e:     # the rest of the site still publishes; the Formats view says the sheet isn't connected
            print(f"::warning title=formats sheet::could not read FORMATS_SHEET_CSV_URL ({type(e).__name__})")
    if not text and FORMATS_CSV.exists():
        text = FORMATS_CSV.read_text(encoding="utf-8")
    if not text:
        return []

    def num(x, strip):
        t = "".join(ch for ch in str(x or "") if ch not in strip).strip()
        try:
            return None if t in ("", "-") else float(t)
        except ValueError:
            return None
    rows = list(csv.reader(io.StringIO(text)))[1:]
    return [{"formula": r[0].strip(), "change_pct": num(r[1] if len(r) > 1 else "", "%, "),
             "outlier": num(r[2] if len(r) > 2 else "", "x× "), "triggers": (r[3] if len(r) > 3 else "").strip()}
            for r in rows if r and r[0].strip()]


def build(con, out_dir: Path = OUT, source: str = "live") -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    label, sector = _labels()
    sets = {
        "niches": niches(con, label, sector),
        "index_daily": index_daily(con, sector),
        "screener": screener(con, label),
        "alerts": alerts(con, label),
        "news": news(con),
        "methodology": methodology(),
        "coverage": coverage.rows(con) if source == "live" else [],   # screening counts are real; kept off the synthetic site
        "formats_sheet": formats_sheet(),
    }
    for name, rows in sets.items():
        (out_dir / f"{name}.json").write_text(json.dumps([{k: _clean(v) for k, v in r.items()} for r in rows], default=str))
    as_of = con.execute("SELECT max(snapshot_date) FROM channel_snapshots").fetchone()[0] if _has(con, "channel_snapshots") else None
    meta = {"source": source, "as_of": _clean(as_of), "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "datasets": list(sets), "rows": {k: len(v) for k, v in sets.items()}}
    (out_dir / "manifest.json").write_text(json.dumps(meta, indent=1))
    return meta
