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

from . import coverage, holders, rates, surface

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
             "risk": r["risk"], "volatility": r["volatility"], "cr3": r["cr3"],
             "views_28d": r.get("views_28d"), "uploads_28d": r.get("uploads_28d")}
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
    for r in _rows(con, "SELECT * FROM news_ranked WHERE category IS NOT NULL ORDER BY published_at DESC LIMIT 150"):
        p = r.get("published_at")
        out.append({"url": r["url"], "title": r["title"], "source": r.get("source") or "",
                    "published": p.strftime("%Y-%m-%d %H:%M") if p else "", "category": r.get("category") or "",
                    "platform": r.get("platform") or "", "impact": r.get("impact") or "", "sentiment": r.get("sentiment") or "",
                    "creators": r.get("creators") or "", "score": r.get("score"),
                    "note": r.get("note") or "", "label": r.get("label") or "", "status": r.get("status") or ""})
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


PLATFORM_NAMES = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}


def platform_overview(con) -> list[dict]:
    """9.1 / 9.3: per platform, tracked creators and followers, attention over the last 28 days vs the 28 before, composite weight."""
    if not _has(con, "platform_daily"):
        return []
    rows = _rows(con, """
        WITH t AS (SELECT d, platform, sum(a) AS a, max(creators) AS creators, max(followers) AS followers
                   FROM platform_daily WHERE level = 0 GROUP BY ALL),
             last AS (SELECT max(d) AS m FROM t)
        SELECT platform,
               max(creators) FILTER (WHERE d = (SELECT m FROM last))  AS creators,
               max(followers) FILTER (WHERE d = (SELECT m FROM last)) AS followers,
               sum(a) FILTER (WHERE d > (SELECT m FROM last) - 28)                                  AS attention_28d,
               sum(a) FILTER (WHERE d <= (SELECT m FROM last) - 28 AND d > (SELECT m FROM last) - 56) AS attention_prev_28d
        FROM t GROUP BY platform""")
    weights = {}
    if _has(con, "composite_index"):
        weights = dict(con.execute("""SELECT platform, weight FROM composite_index
                                      WHERE level = 0 AND platform <> 'ALL' AND d = (SELECT max(d) FROM composite_index)""").fetchall())
    out = []
    for r in rows:
        prev = r["attention_prev_28d"]
        out.append({"platform": PLATFORM_NAMES.get(r["platform"], r["platform"]), "creators": r["creators"], "followers": r["followers"],
                    "attention_28d": r["attention_28d"], "chg": (r["attention_28d"] / prev - 1) if prev else None,
                    "weight": weights.get(r["platform"])})
    return sorted(out, key=lambda r: -(r["followers"] or 0))


def platform_series(con) -> list[dict]:
    """Each platform's daily attention, 7-day average, indexed to 100 at the start of the window: platforms count views
    differently, so the chart compares movement, never levels (9.2)."""
    if not _has(con, "platform_daily"):
        return []
    rows = _rows(con, """
        WITH t AS (SELECT d, platform, sum(a) AS a FROM platform_daily WHERE level = 0 GROUP BY ALL),
             s AS (SELECT d, platform, avg(a) OVER (PARTITION BY platform ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS a7,
                          count(*) OVER (PARTITION BY platform ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS n
                   FROM t),
             w AS (SELECT * FROM s WHERE n = 7 AND d > (SELECT max(d) FROM t) - 120)
        SELECT d, platform, 100 * a7 / first_value(a7) OVER (PARTITION BY platform ORDER BY d) AS idx FROM w ORDER BY d""")
    # same shape as index_daily so the page's chart draws it
    return [{"day": r["d"].isoformat(), "sector": PLATFORM_NAMES.get(r["platform"], r["platform"]), "avg_ccos": r["idx"]} for r in rows]


def platform_lead(con, label) -> list[dict]:
    """9.4: which platform moves first in a topic. Sectors, categories and niches with a clear signal (rho 0.3+, 16+ weeks)."""
    if not _has(con, "platform_lead"):
        return []
    rows = _rows(con, """SELECT * FROM platform_lead WHERE rho >= 0.3 AND n >= 16 AND length(code) <= 9
                         ORDER BY rho DESC LIMIT 60""")
    return [{"code": r["code"], "topic": label(r["code"]), "leader": PLATFORM_NAMES.get(r["leader"], r["leader"]),
             "follower": PLATFORM_NAMES.get(r["follower"], r["follower"]), "lag_weeks": r["best_lag_weeks"], "rho": r["rho"], "n": r["n"]}
            for r in rows]


def platform_movement(con, label) -> list[dict]:
    """9.2: this week's attention on each platform as a z-score against that platform's own last 26 weeks, per category and niche."""
    if not _has(con, "platform_z"):
        return []
    rows = _rows(con, """SELECT code, level, platform, z FROM platform_z
                         WHERE week = (SELECT max(week) FROM platform_z WHERE days = 7) AND level IN (2, 3)""")
    acc = {}
    for r in rows:
        a = acc.setdefault(r["code"], {"code": r["code"], "topic": label(r["code"]), "level": r["level"],
                                       "youtube": None, "tiktok": None, "instagram": None})
        a[r["platform"]] = r["z"]
    return sorted(acc.values(), key=lambda r: r["code"])


def _cvs_config() -> dict:
    """Pillar maximums, gate thresholds and tier rules, so the tear sheet explains the score with the numbers that made it."""
    import yaml
    c = (yaml.safe_load((ROOT / "config" / "formulas.yaml").read_text()) or {}).get("cvs", {})
    return {"weights": c.get("weights", {}), "gates": c.get("gates", {}), "tiers": c.get("tiers", {})}


def creator_details(con, label) -> list[dict]:
    """Everything the Comp view compares about one creator: the screener row, creator metrics (5.x), the viability
    score and its pillars, and the 90-day earnings range (labeled placeholder until real RPM bands are set)."""
    if not _has(con, "screener"):
        return []
    have = {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    joins, cols = [], []
    if "creator_metrics" in have:
        joins.append("LEFT JOIN creator_metrics m USING (channel_id)")
        cols += ["m.views_28d", "m.uploads_per_week", "m.regularity", "m.consistency", "m.top_video_share", "m.relative_er", "m.last_upload"]
    if "creator_watchlist" in have:
        joins.append("LEFT JOIN creator_watchlist w USING (channel_id)")
        cols += ["w.cvs", "w.tier", "w.momentum", "w.consistency_pillar", "w.audience_depth", "w.market_opportunity", "w.hit_dependence"]
    if "creator_watchlist" in have:
        cols += ["w.auto_gates", "w.manual_gates", "w.needs_review"]
    if "creator_earnings" in have:
        joins.append("LEFT JOIN creator_earnings e USING (channel_id)")
        cols += ["e.earnings_low_90d", "e.earnings_high_90d", "e.rpm_source"]
    sql = f"SELECT s.*{''.join(', ' + c for c in cols)} FROM screener s {' '.join(joins)}"
    out = []
    for r in _rows(con, sql):
        d = {"channel": r["title"], "channel_id": r["channel_id"], "niche": label(r["niche_code"]) if r["niche_code"] else "—",
             "niche_code": r["niche_code"]}
        for k in ("subscribers", "views", "view_growth_28d", "sub_growth_28d", "growth_vs_niche", "uploads_28d", "median_video_views",
                  "outlier_rate", "views_28d", "uploads_per_week", "regularity", "consistency", "top_video_share", "relative_er",
                  "last_upload", "cvs", "tier", "momentum", "consistency_pillar", "audience_depth", "market_opportunity",
                  "hit_dependence", "earnings_low_90d", "earnings_high_90d", "rpm_source", "auto_gates", "manual_gates", "needs_review"):
            if k in r:
                d[k] = r[k]
        out.append(d)
    # what the tear sheet adds: the brands and companies named in the creator's videos (6.2) and how they package them (2.11)
    by = {d["channel_id"]: d for d in out}
    if "video_entities" in have:
        for cid, ents in con.execute("""
                SELECT channel_id, list(entity ORDER BY n DESC)[1:5] FROM (
                  SELECT v.channel_id, e.entity, count(*) AS n FROM video_entities e JOIN videos v USING (video_id)
                  WHERE v.published_at >= (SELECT max(published_at) FROM videos) - INTERVAL 180 DAY
                  GROUP BY ALL) GROUP BY channel_id""").fetchall():
            if cid in by:
                by[cid]["top_entities"] = ", ".join(ents)
    if "video_packaging" in have:
        for cid, labels, shares in con.execute("""
                WITH c AS (SELECT v.channel_id, p.packaging_label, count(*) AS n
                           FROM video_packaging p JOIN videos v USING (video_id) WHERE p.packaging_label IS NOT NULL GROUP BY ALL),
                     t AS (SELECT *, n / sum(n) OVER (PARTITION BY channel_id) AS share FROM c)
                SELECT channel_id, list(packaging_label ORDER BY n DESC)[1:3], list(share ORDER BY n DESC)[1:3] FROM t
                GROUP BY channel_id""").fetchall():
            if cid in by:
                by[cid]["top_packaging"] = ", ".join(f"{l} {round(100 * float(x))}%" for l, x in zip(labels, shares))
    return out


def corp(con, details: list[dict]) -> list[dict]:
    """One row per creator in the Creator Companies sheet: the stage ladder and financing, next to the terminal's numbers
    for that creator (when we track them), so the Undercapitalized screen can rank strong audiences without a company."""
    if not _has(con, "creator_companies"):
        return []
    by_id = {d["channel_id"]: d for d in details}
    n_co = dict(con.execute("SELECT creator, count(*) FROM creator_company_list GROUP BY 1").fetchall()) if _has(con, "creator_company_list") else {}
    last = dict(con.execute("SELECT creator, max(round_date) FROM creator_rounds GROUP BY 1").fetchall()) if _has(con, "creator_rounds") else {}
    out = []
    for r in _rows(con, "SELECT * FROM creator_companies"):
        d = by_id.get(r["channel_id"]) or {}
        out.append({**r, "channel": d.get("channel") or r["creator"], "niche": d.get("niche"), "niche_code": d.get("niche_code"),
                    "subscribers": d.get("subscribers"), "view_growth_28d": d.get("view_growth_28d"),
                    "growth_vs_niche": d.get("growth_vs_niche"), "cvs": d.get("cvs"), "tier": d.get("tier"),
                    "tracked": bool(d), "companies": n_co.get(r["creator"], 0), "last_round": last.get(r["creator"])})
    return out


def business(con, label) -> list[dict]:
    """Revenue streams and sponsors per creator, from the description parser (videos in the last 90 days)."""
    if not _has(con, "creator_business") or not _has(con, "screener"):
        return []
    rows = _rows(con, """SELECT b.*, s.title AS channel, s.niche_code, s.subscribers FROM creator_business b
                         JOIN screener s USING (channel_id) ORDER BY b.sponsored_share DESC, s.subscribers DESC""")
    for r in rows:
        r["niche"] = label(r["niche_code"])
        r["streams"] = [x for x in (r["streams"] or [])]
        r["top_sponsors"] = list(r["top_sponsors"] or [])
    return rows


def sponsors(con, label) -> list[dict]:
    if not _has(con, "sponsor_map"):
        return []
    rows = _rows(con, "SELECT * FROM sponsor_map WHERE creators >= 2 ORDER BY creators DESC, videos DESC LIMIT 300")
    for r in rows:
        r["niches"] = "; ".join(sorted({label(c) for c in (r.pop("niche_codes") or []) if c} - {None}))
    return rows


def risk(con, label) -> list[dict]:
    if not _has(con, "creator_risk") or not _has(con, "screener"):
        return []
    rows = _rows(con, """SELECT r.*, s.title AS channel, s.subscribers FROM creator_risk r JOIN screener s USING (channel_id)
                         ORDER BY r.growth_per_risk DESC NULLS LAST""")
    for r in rows:
        r["niche"] = label(r["niche_code"])
    return rows


def corp_companies(con) -> list[dict]:
    return _rows(con, "SELECT * FROM creator_company_list") if _has(con, "creator_company_list") else []


def corp_rounds(con) -> list[dict]:
    return _rows(con, "SELECT * FROM creator_rounds ORDER BY round_date DESC NULLS LAST") if _has(con, "creator_rounds") else []


def _demo_corp(details: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Made-up company stages, companies and rounds for the synthetic creators, so the demo shows every screen."""
    import zlib
    inv = ["Example Ventures", "Sample Growth Partners", "Placeholder Capital", "Demo Strategic Fund", "Mock Media Holdings"]
    cut = [(40, None), (50, 1), (65, 2), (85, 3), (93, 4), (99, 5), (100, 6)]
    corp_rows, cos, rounds = [], [], []
    for d in details[:80]:
        h = zlib.crc32(d["channel_id"].encode()) % 100
        st = next(s for c, s in cut if h < c)
        name = d["channel"]
        co = None if st in (None, 1) else f"{name} Media Co." if st in (2, 3) else f"{name} Holdings"
        raised = None
        if co:
            cos.append({"company": co, "creator": name, "relationship": "Owns", "type": "Holding" if st >= 4 else "Operating",
                        "parent": None, "what": "SYNTHETIC: made-up company", "status": "Sold" if st == 6 else "Active",
                        "source": None, "confidence": "Inferred", "last_checked": None})
        if st in (5, 6):
            raised = float(2 + h % 18)
            rounds.append({"round_date": f"2025-{1 + h % 12:02d}-15", "company": co, "creator": name, "round": "Seed" if h % 2 else "Series A",
                           "amount_m": raised, "valuation_m": raised * 6, "currency": "USD", "lead_investors": inv[h % len(inv)],
                           "other_investors": inv[(h + 2) % len(inv)], "source": None, "confidence": "Inferred", "last_checked": None})
        corp_rows.append({"creator": name, "channel_id": d["channel_id"], "in_terminal": "Yes", "stage": st,
                          "stage_name": {1: "No company found", 2: "Payout LLC", 3: "Operating company", 4: "Holding company",
                                         5: "Outside capital", 6: "Exited"}.get(st),
                          "main_company": co, "outside_money": None if st is None else "Yes" if raised else "None found",
                          "raised_usd_m": raised, "research_status": "To research" if st is None else "Done",
                          "confidence": None if st is None else "Inferred", "source": None, "notes": "SYNTHETIC: made-up company data",
                          "last_checked": None, "channel": name, "niche": d.get("niche"), "niche_code": d.get("niche_code"),
                          "subscribers": d.get("subscribers"), "view_growth_28d": d.get("view_growth_28d"),
                          "growth_vs_niche": d.get("growth_vs_niche"), "cvs": d.get("cvs"), "tier": d.get("tier"), "tracked": True,
                          "companies": 1 if co else 0, "last_round": rounds[-1]["round_date"] if raised else None})
    return corp_rows, cos, rounds


def creator_series(con) -> list[dict]:
    """The tear sheet's chart: each creator's last 90 days of views gained and subscribers, one row per creator."""
    if not _has(con, "channel_daily"):
        return []
    rows = con.execute("""
        SELECT channel_id, min(snapshot_date), list(views_gained ORDER BY snapshot_date), list(subscribers ORDER BY snapshot_date)
        FROM channel_daily WHERE snapshot_date > (SELECT max(snapshot_date) FROM channel_snapshots) - 90
        GROUP BY channel_id""").fetchall()
    return [{"channel_id": c, "start": d.isoformat(), "views": v, "subs": s} for c, d, v, s in rows]


# ---------------------------------------------------------------- synthetic-only datasets (the demo site)
def _demo_news(as_of: dt.date, creators: list[str]) -> list[dict]:
    import random
    rng = random.Random(7)
    items = [
        ("PLATFORM", "YOUTUBE", "HIGH", "POSITIVE", "YouTube widens mid-roll eligibility to videos over six minutes"),
        ("MONETIZATION", "TIKTOK", "HIGH", "NEGATIVE", "TikTok trims Creator Rewards rates in three content categories"),
        ("DEAL", "ALL", "MEDIUM", "POSITIVE", "{c} closes seed round for a kitchenware line"),
        ("POLICY", "ALL", "HIGH", "NEUTRAL", "FTC proposes updated disclosure rules for creator endorsements"),
        ("CREATOR", "YOUTUBE", "MEDIUM", "POSITIVE", "{c} passes one million subscribers after a 90-day series"),
        ("RESEARCH", "ALL", "LOW", "NEUTRAL", "Survey: two-thirds of mid-size creators now run a second business"),
        ("TREND", "INSTAGRAM", "MEDIUM", "POSITIVE", "Long carousels are outdrawing Reels in fitness and home cooking"),
        ("PLATFORM", "INSTAGRAM", "MEDIUM", "NEUTRAL", "Instagram tests a creator subscription bundle in four markets"),
        ("MONETIZATION", "YOUTUBE", "MEDIUM", "POSITIVE", "YouTube Shopping affiliate program opens to channels under 10,000 subscribers"),
        ("DEAL", "ALL", "HIGH", "POSITIVE", "Holding company acquires a minority stake in {c}'s media business"),
        ("CREATOR", "TIKTOK", "MEDIUM", "NEGATIVE", "{c} pauses uploads after a sponsor dispute"),
        ("POLICY", "TIKTOK", "HIGH", "NEGATIVE", "State attorneys general open an inquiry into TikTok's creator fund terms"),
        ("RESEARCH", "YOUTUBE", "LOW", "NEUTRAL", "Study finds watch time on personal finance videos up 18% year over year"),
        ("TREND", "TIKTOK", "MEDIUM", "POSITIVE", "Sleep and recovery content is the fastest-growing wellness niche this quarter"),
        ("DEAL", "ALL", "MEDIUM", "NEUTRAL", "{c} signs a licensing deal for a back-catalog of tutorials"),
        ("PLATFORM", "YOUTUBE", "LOW", "NEUTRAL", "YouTube Studio adds a revenue forecast panel for Partner Program members"),
        ("MONETIZATION", "INSTAGRAM", "LOW", "NEGATIVE", "Brand deal rates for mid-tier Instagram creators fell in Q3, agency data shows"),
        ("CREATOR", "YOUTUBE", "HIGH", "POSITIVE", "{c} launches a holding company for three channels and a product line"),
        ("TREND", "YOUTUBE", "MEDIUM", "NEUTRAL", "Side-hustle explainers cool after a summer spike"),
        ("RESEARCH", "ALL", "MEDIUM", "NEUTRAL", "New index tracks creator-led consumer brands against the S&P 500"),
        ("POLICY", "YOUTUBE", "MEDIUM", "NEUTRAL", "YouTube updates its reused-content policy for compilation channels"),
        ("DEAL", "ALL", "LOW", "POSITIVE", "{c} partners with a regional grocery chain on a recipe series"),
        ("CREATOR", "INSTAGRAM", "LOW", "NEGATIVE", "{c} loses a skincare sponsorship after a product mix-up"),
        ("TREND", "ALL", "MEDIUM", "POSITIVE", "Budgeting content climbs as holiday spending forecasts tighten"),
    ]
    weight = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    end = dt.datetime.combine(as_of, dt.time(14, 0))
    out = []
    for i, (cat, plat, imp, sent, title) in enumerate(items):
        c = rng.choice(creators) if "{c}" in title and creators else ""
        when = end - dt.timedelta(hours=rng.randint(2, 20 * 24))
        hours = (end - when).total_seconds() / 3600
        out.append({"url": f"https://example.com/demo-news/{i + 1}", "title": title.format(c=c), "source": "Demo Wire (synthetic)",
                    "published": when.strftime("%Y-%m-%d %H:%M"), "category": cat, "platform": plat, "impact": imp,
                    "sentiment": sent, "creators": c, "score": weight[imp] * 0.5 ** (hours / 168)})
    return out


def _demo_formats() -> list[dict]:
    import random
    rng = random.Random(11)
    patterns = [
        ("I tried {x} for 30 days", "Challenge, Transformation, Curiosity"), ("{x} vs {y}: which is actually worth it?", "Comparison, Value"),
        ("The {x} nobody talks about", "Secret, Curiosity"), ("Why I stopped {x}", "Confession, Contrarian"),
        ("{n} {x} mistakes beginners make", "List, Fear of missing out"), ("I asked {n} experts about {x}", "Authority, Curiosity"),
        ("$1 vs $1,000 {x}", "Price contrast, Comparison"), ("What {x} really costs in 2026", "Money, Transparency"),
        ("Do this before you {x}", "Urgency, Advice"), ("The only {x} guide you need", "Completeness, Authority"),
        ("{x} in {n} minutes a day", "Ease, Time"), ("I copied a {x} routine for a week", "Challenge, Imitation"),
        ("Ranking every {x}", "List, Comparison"), ("Stop {x} like this", "Correction, Fear"), ("How I {x} on a budget", "Money, How-to"),
        ("{x} changed my life (not clickbait)", "Transformation, Trust"), ("Testing viral {x} hacks", "Curiosity, Proof"),
        ("The science of {x}", "Authority, Explanation"), ("Day in the life: {x}", "Voyeurism, Routine"),
        ("I quit {x} for {n} days", "Challenge, Abstinence"), ("Beginner to pro {x} in {n} weeks", "Transformation, Progress"),
        ("Honest review: {x}", "Trust, Review"), ("What nobody tells you about {x}", "Secret, Warning"),
        ("{x} tier list", "List, Opinion"), ("Rich vs poor {x}", "Price contrast, Social"),
    ]
    topics = ["meal prep", "pilates", "budgeting", "cold plunges", "sleep tracking", "sourdough", "index funds", "home gyms",
              "skincare", "credit cards", "standing desks", "air fryers"]
    out = []
    for i, (p, trig) in enumerate(patterns):
        x, y = rng.sample(topics, 2)
        change = round(rng.lognormvariate(4.0, 0.8))
        outlier = round(rng.lognormvariate(1.3, 0.5), 1)
        out.append({"formula": p.format(x=x, y=y, n=rng.choice([3, 5, 7, 10])), "change_pct": float(change),
                    "outlier": outlier, "triggers": trig})
    return out


def _demo_alerts(screener: list[dict]) -> list[dict]:
    """A handful of spikes so the Alerts screen has rows: 3 to 6 sigma, 2.2x to 5x a typical day."""
    import random
    rng = random.Random(3)
    out = []
    for r in rng.sample(screener, min(5, len(screener))):
        typical = rng.randint(8_000, 120_000)
        mult = rng.uniform(2.2, 5.0)
        out.append({"channel": r["channel"], "channel_id": r["channel_id"], "niche": r["niche"],
                    "views_today": round(typical * mult), "typical_daily_views": typical, "z_score": round(rng.uniform(3.1, 6.2), 1)})
    return sorted(out, key=lambda x: -x["z_score"])


def _demo_coverage(con, label) -> list[dict]:
    import random
    rng = random.Random(5)
    counts = dict(con.execute("SELECT substr(niche_code, 1, 5), count(*) FROM channel_niche GROUP BY 1").fetchall()) \
        if _has(con, "channel_niche") else {}
    out = []
    for code in sorted(counts):
        tracked = counts[code]
        screened = tracked * rng.randint(4, 7)
        kept = tracked + rng.randint(0, 4)
        review = rng.randint(2, 12)
        dropped = screened - kept - review
        split = [rng.random() for _ in range(8)]
        drops = [int(dropped * s / sum(split)) for s in split]
        drops[0] += dropped - sum(drops)
        row = {"niche_code": code, "niche": label(code), "tracked": tracked, "anchors": rng.randint(2, 5), "added_7d": rng.randint(0, 3),
               "screened": screened, "kept": kept, "review": review, "dropped": dropped}
        row.update({f"drop_{k}": v for k, v in zip(coverage.DROP_REASONS, drops)})
        out.append(row)
    return out


def build(con, out_dir: Path = OUT, source: str = "live", public: bool = False) -> dict:
    """public=True is the shareable build (the beta at terminal.cvaccelerator.com): Creator Capital notes that are still
    drafts are left out, so a guest never sees unposted work."""
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
        "creator_details": creator_details(con, label),
        "creator_series": creator_series(con),
        "platform_overview": platform_overview(con),
        "platform_series": platform_series(con),
        "platform_lead": platform_lead(con, label),
        "platform_movement": platform_movement(con, label),
        "surface": surface.cells(con, label, sector),
        "skew": surface.skew(con, label, sector),
    }
    rpm = rates.latest_rpm(con, label)
    if source == "synthetic":
        codes = [r[0] for r in con.execute("SELECT DISTINCT niche_code FROM channel_niche").fetchall()] if _has(con, "channel_niche") else []
        as_of_r = con.execute("SELECT max(snapshot_date) FROM channel_snapshots").fetchone()[0] or dt.date.today()
        rpm = rates.demo_rpm([(c, label(c)) for c in codes], as_of_r)
    # the policy rates are public facts with sources, the same on both sites; the RPM side is synthetic on the demo
    sets["rates"] = rates.board(rpm=rpm)
    sets["rate_changes"] = rates.changes()
    sets["niche_rpm"] = rpm
    sets["business"] = business(con, label)
    sets["risk"] = risk(con, label)
    sets["sponsors"] = sponsors(con, label)
    sets["corp"] = corp(con, sets["creator_details"])
    sets["corp_companies"] = corp_companies(con)
    sets["corp_rounds"] = corp_rounds(con)
    if source == "synthetic":
        # the demo shows every screen filled in: made-up news, title formats and screening counts, all labeled synthetic
        as_of_d = con.execute("SELECT max(snapshot_date) FROM channel_snapshots").fetchone()[0] or dt.date.today()
        sets["news"] = _demo_news(as_of_d, [r["channel"] for r in sets["screener"][:30]])
        sets["formats_sheet"] = _demo_formats()
        sets["coverage"] = _demo_coverage(con, label)
        if not sets["alerts"]:
            sets["alerts"] = _demo_alerts(sets["screener"])
        sets["corp"], sets["corp_companies"], sets["corp_rounds"] = _demo_corp(sets["creator_details"])
    hold = holders.holdings(sets["corp_rounds"], {} if source == "synthetic" else None)
    if source == "synthetic":
        hold = holders.demo_types(hold)
    sets["holdings"] = hold
    sets["holders"] = holders.holders(hold, {} if source == "synthetic" else None)
    if public:
        sets["news"] = [n for n in sets["news"] if n.get("status") != "draft"]
    for name, rows in sets.items():
        (out_dir / f"{name}.json").write_text(json.dumps([{k: _clean(v) for k, v in r.items()} for r in rows], default=str))
    as_of = con.execute("SELECT max(snapshot_date) FROM channel_snapshots").fetchone()[0] if _has(con, "channel_snapshots") else None
    meta = {"source": source, "as_of": _clean(as_of), "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "datasets": list(sets), "rows": {k: len(v) for k, v in sets.items()},
            "cvs": _cvs_config(), "public": public}
    (out_dir / "manifest.json").write_text(json.dumps(meta, indent=1))
    return meta
