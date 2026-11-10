"""Plain-English views for asking questions in chat (Claude + the MotherDuck connector).

The working tables use internal codes (niche 03.07.013) and short column names. These views spell everything out:
names instead of codes, readable column names, and a comment on every view and key column, so a question like
"which fitness creators under 500K are outgrowing their niche?" maps onto one obvious query.

    ask_guide             start here: one row per view, what it holds and how to use it
    ask_creators          one row per tracked creator, every creator metric with niche and sector names
    ask_niches            one row per niche, today's opportunity metrics
    ask_niche_history     the same niche metrics, one row per niche per day
    ask_creator_history   each creator's daily subscribers and views gained
    ask_topic_attention   attention, growth and demand signal for every CTNCS topic at every level, latest day
    ask_news              the News feed, including Creator Capital daily notes
    ask_tier_changes      creators moving between Watch, Priority and Diligence-ready
    ask_niche_skew        reach by creator size per niche: the skew screen
    ask_platform_rates    each platform's creator share, last and next change (the rates board)
    ask_rate_changes      every dated change to a platform's creator share
    ask_niche_rpm         YouTube RPM by niche from TubeLab estimates
    ask_creator_companies the company around each creator: stage, companies, financing (Creator Companies sheet)
    ask_creator_rounds    financing rounds for creator companies, with investors
    ask_glossary          every metric: what it measures, the formula, how to read it
    ask_topics            the CTNCS codebook: code, name, level, sector, full path

Rebuilt by the pipeline's `ask` stage after every compute. Read-only by design: nothing here is a source of truth.
"""
import csv
from pathlib import Path

from . import rates, surface

ROOT = Path(__file__).resolve().parent.parent

GUIDE = [
    ("ask_platform_rates", "One row per platform monetization program: the creator's published share (or dollar rate), whether it is disclosed, the last change and the next scheduled one, with a source and verified date.",
     "creator_share is a percent when unit is %, or dollars per unit (Roblox $/Robux). disclosed = No means the platform publishes no share (TikTok, Spotify); use ask_niche_rpm for what creators actually earn on YouTube."),
    ("ask_rate_changes", "Every dated change to a platform's creator share, effective or scheduled (the decisions log).", "Join to ask_platform_rates by program id through the board; status = scheduled are announced future changes."),
    ("ask_niche_rpm", "YouTube RPM by niche (dollars per 1,000 views), from TubeLab's channel estimates, latest monthly pull.", "Estimates, not reported earnings: rpm_mid is the median of each sampled channel's estimated range."),
    ("ask_niche_skew", "One row per niche: reach (median views per 100 subscribers, videos up to 3 months old) for the smallest, middle and biggest third of creators by subscribers, the skew (small over big) and dispersion (spread of ln views).",
     "skew above 1 means small creators get more views per subscriber than big ones; the steeper it is, the more the niche still rewards new supply (the undersaturation signal). Thirds are within each niche. reach_3m_vs_first_week shows how much a video keeps earning after launch."),
    ("ask_creators", "One row per tracked creator: size, growth, consistency, hits, the Creator Viability Score (CVS) and its pillars, tier, gates and a 90-day earnings range.",
     "Growth figures need 28 days of history; CVS and tiers fill in after that. growth_vs_niche > 0 means the creator is outgrowing the median creator in its niche. Earnings use placeholder RPM bands when rpm_source = 'placeholder': treat as illustrative."),
    ("ask_niches", "One row per niche (CTNCS level 3) with today's opportunity metrics.",
     "opportunity_score (CCOS) = 20 x growth_7d_ratio x (1 - saturation); a flat niche at median saturation scores 10. attention_change_7d is views this week vs last week. Needs 14 days of history."),
    ("ask_niche_history", "The niche metrics for every day, for trends over time.", "Filter by niche or sector and order by day."),
    ("ask_creator_history", "Each creator's subscribers, total views, and views and subscribers gained per day.",
     "views_gained is the change since the previous snapshot; a missed day shows as a larger gain the next day (days_since_prev > 1)."),
    ("ask_topic_attention", "Attention (views gained), week-over-week growth, share of parent topic and the Demand Signal Score for every CTNCS topic at every level, on the latest day.",
     "level 1 = sector, 2 = category, 3 = niche, 4 = sub-niche. thin = true means too few creators or videos to trust."),
    ("ask_news", "The News feed: platform and trade-press headlines plus Creator Capital daily notes, tagged by category, platform, impact and sentiment.",
     "source = 'Creator Capital' rows carry Emily's full note in the note column; status = 'draft' means not yet posted."),
    ("ask_tier_changes", "Creators entering or leaving a CVS tier (Watch, Priority, Diligence-ready), by day.", "Empty until scores have 28 days of history."),
    ("ask_creator_companies", "The company around each creator from the Creator Companies sheet: stage 1 no company found, 2 payout LLC, 3 operating company, 4 holding company, 5 outside capital, 6 exited; main company, disclosed money raised, confidence and source, joined to the creator's growth.",
     "stage is null when research_status = 'To research': that means unknown, not no company. confidence: Confirmed (filing or company), Reported (press), Inferred (needs confirming). Public rows only."),
    ("ask_creator_rounds", "Financing rounds for companies around creators: date, company, round, amount and valuation in millions with currency, lead and other investors (semicolon-separated), source.",
     "amount_m is null when the round size was not disclosed. Sum amounts only within one currency."),
    ("ask_glossary", "Every metric in the terminal: code, name, what it measures, the formula, how to read it, and whether it is running on live data (Built) or waiting for an input (Ready).",
     "The Methodology Handbook is the published version of this table."),
    ("ask_topics", "The CTNCS codebook: every topic code with its name, level, sector and full path.", "Join on code to name any code in the working tables."),
]

COMMENTS = {
    "ask_creators": {
        "creator": "Channel name", "niche": "The creator's primary niche (CTNCS level 3 name)", "sector": "CTNCS sector name",
        "subscribers": "Subscribers today (YouTube rounds to 3 significant figures)", "views_28d": "Views gained in the last 28 days",
        "view_growth_28d": "Views in the last 28 days vs the 28 before, as a fraction (0.10 = +10%)",
        "growth_vs_niche": "view_growth_28d minus the niche median; positive = outgrowing the niche",
        "outlier_rate": "Share of recent videos with at least 3x the creator's typical views at the same age",
        "top_video_share": "Share of recent views from the single biggest video; lower = less reliance on one hit",
        "engagement_vs_niche": "Engagement rate relative to the niche (1.00 = niche average)",
        "cvs": "Creator Viability Score, 0-100: momentum 25, consistency 20, audience depth 20, market opportunity 20, hit independence 15",
        "tier": "Watch (60+), Priority (75+ held 14 days), Diligence-ready (85+ held 28 days and manual review passed); null = no tier",
        "passes_gates": "Automatic gates: 25K+ subscribers, 250K+ views in 28 days, an upload in the last 30 days",
        "earnings_low_90d": "Low end of estimated YouTube ad earnings over 90 days (see rpm_source)",
        "earnings_high_90d": "High end of estimated YouTube ad earnings over 90 days (see rpm_source)",
    },
    "ask_niches": {
        "opportunity_score": "CCOS = 20 x growth_7d_ratio x (1 - saturation); 10 = flat niche at median saturation",
        "saturation": "SI, 0-1: uploads per 1,000 views gained over 28 days, scaled so the median niche is 0.5",
        "growth_7d_ratio": "NGP: views gained in the last 7 days / the 7 days before (1.00 = flat)",
        "attention_change_7d": "growth_7d_ratio - 1, as a fraction (0.10 = views up 10% week over week)",
        "content_minutes_28d": "TVE: minutes of video published by tracked creators in the niche over 28 days",
        "top3_share": "Share of the niche's views going to its top 3 creators (concentration)",
        "risk": "LOW, MEDIUM or HIGH, from volatility of daily views plus top-3 concentration",
    },
}


def _has(con, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0])


def _topics(con) -> None:
    from . import ctncs
    try:
        book = ctncs.load()
    except Exception:
        book = {}
    rows = []
    for code, r in book.items():
        path = ctncs.label_path(book, code)
        rows.append((code, r.get("label"), int(r.get("level") or 0), r.get("parent_code") or None,
                     path[0] if path else None, " > ".join(path)))
    con.execute("CREATE OR REPLACE TABLE ask_topics (code VARCHAR, name VARCHAR, level INTEGER, parent_code VARCHAR, sector VARCHAR, path VARCHAR)")
    if rows:
        con.executemany("INSERT INTO ask_topics VALUES (?, ?, ?, ?, ?, ?)", rows)


def _glossary(con) -> None:
    con.execute("CREATE OR REPLACE TABLE ask_glossary (code VARCHAR, name VARCHAR, section VARCHAR, handbook_ref VARCHAR, measures VARCHAR, "
                "formula VARCHAR, inputs VARCHAR, how_to_read VARCHAR, status VARCHAR)")
    with open(ROOT / "config" / "methodology.csv", newline="", encoding="utf-8") as f:
        rows = [(r["code"], r["name"], r["section"], r["ref"], r["measures"], r["formula"], r["inputs"], r["read"], r["status"])
                for r in csv.DictReader(f)]
    con.executemany("INSERT INTO ask_glossary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def _rates(con) -> None:
    con.execute("""CREATE OR REPLACE TABLE ask_platform_rates (platform VARCHAR, program VARCHAR, revenue_type VARCHAR,
        creator_share DOUBLE, unit VARCHAR, disclosed VARCHAR, what_it_is VARCHAR, note VARCHAR, last_change_date VARCHAR,
        last_change DOUBLE, last_change_unit VARCHAR, last_change_what VARCHAR, next_change_date VARCHAR, next_change_what VARCHAR,
        verified VARCHAR, stale BOOLEAN, source VARCHAR)""")
    con.executemany("INSERT INTO ask_platform_rates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(r["platform"], r["program"], r["revenue"], r["level"], r["unit"] or None, r["disclosed"], r["basis"],
                      r["note"] or None, r["last_date"], r["last_change"], r["last_change_unit"], r["last_what"], r["next_date"],
                      r["next_what"], r["verified"], r["stale"], r["source"]) for r in rates.board()])
    con.execute("""CREATE OR REPLACE TABLE ask_rate_changes (program_id VARCHAR, date VARCHAR, old_level DOUBLE, new_level DOUBLE,
        status VARCHAR, what_changed VARCHAR, source VARCHAR)""")
    con.executemany("INSERT INTO ask_rate_changes VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(c["id"], c["date"], c["old"], c["new"], c["status"], c["what"], c["source"]) for c in rates.changes()])


def build(con) -> list[str]:
    _topics(con)
    _glossary(con)
    _rates(con)
    made = ["ask_topics", "ask_glossary", "ask_platform_rates", "ask_rate_changes"]
    have = lambda *t: all(_has(con, x) for x in t)

    if have("screener"):
        m = "LEFT JOIN creator_metrics m USING (channel_id)" if have("creator_metrics") else ""
        w = "LEFT JOIN creator_watchlist w USING (channel_id)" if have("creator_watchlist") else ""
        e = "LEFT JOIN creator_earnings e USING (channel_id)" if have("creator_earnings") else ""
        col = lambda alias, c, name=None: (f"{alias}.{c}" if (alias == "m" and m) or (alias == "w" and w) or (alias == "e" and e) else "NULL") + f" AS {name or c}"
        con.execute(f"""CREATE OR REPLACE VIEW ask_creators AS
            SELECT s.title AS creator, s.channel_id, 'https://www.youtube.com/channel/' || s.channel_id AS youtube_url,
                   t.name AS niche, s.niche_code, t.sector, s.subscribers, s.views AS lifetime_views,
                   {col('m', 'views_28d')}, s.view_growth_28d, s.sub_growth_28d, s.growth_vs_niche, s.uploads_28d,
                   {col('m', 'uploads_per_week')}, {col('m', 'last_upload')}, s.median_video_views, s.outlier_rate,
                   {col('m', 'regularity')}, {col('m', 'consistency')}, {col('m', 'top_video_share')}, {col('m', 'relative_er', 'engagement_vs_niche')},
                   {col('w', 'cvs')}, {col('w', 'tier')}, {col('w', 'momentum')}, {col('w', 'consistency_pillar')}, {col('w', 'audience_depth')},
                   {col('w', 'market_opportunity')}, {col('w', 'hit_dependence', 'hit_independence')}, {col('w', 'auto_gates', 'passes_gates')},
                   {col('w', 'manual_gates', 'manual_review_passed')}, {col('w', 'needs_review')},
                   {col('e', 'earnings_low_90d')}, {col('e', 'earnings_high_90d')}, {col('e', 'rpm_source')}
            FROM screener s {m} {w} {e} LEFT JOIN ask_topics t ON t.code = s.niche_code""")
        made.append("ask_creators")

    if have("niche_opportunity"):
        tracked = ("LEFT JOIN (SELECT niche_code, count(*) AS creators_tracked FROM channel_niche GROUP BY 1) c USING (niche_code)"
                   if have("channel_niche") else "")
        con.execute(f"""CREATE OR REPLACE VIEW ask_niches AS
            SELECT t.name AS niche, n.niche_code, t.sector, {'c.creators_tracked' if tracked else 'NULL AS creators_tracked'},
                   n.views_28d, n.uploads_28d, n.tve AS content_minutes_28d, n.si AS saturation, n.ngp AS growth_7d_ratio,
                   n.ngp - 1 AS attention_change_7d, n.ccos AS opportunity_score, n.ccos_chg_7d AS opportunity_change_7d,
                   n.trend, n.risk, n.volatility, n.cr3 AS top3_share
            FROM niche_opportunity n {tracked} LEFT JOIN ask_topics t ON t.code = n.niche_code""")
        made.append("ask_niches")

    if have("niche_opportunity_daily"):
        con.execute("""CREATE OR REPLACE VIEW ask_niche_history AS
            SELECT n.snapshot_date AS day, t.name AS niche, n.niche_code, t.sector, n.views_28d, n.uploads_28d,
                   n.tve AS content_minutes_28d, n.si AS saturation, n.ngp AS growth_7d_ratio, n.ccos AS opportunity_score,
                   n.ccos_chg_7d AS opportunity_change_7d
            FROM niche_opportunity_daily n LEFT JOIN ask_topics t ON t.code = n.niche_code""")
        made.append("ask_niche_history")

    if have("channel_daily", "channels"):
        con.execute("""CREATE OR REPLACE VIEW ask_creator_history AS
            SELECT d.snapshot_date AS day, c.title AS creator, d.channel_id, d.subscribers, d.views AS lifetime_views,
                   d.views_gained, d.subs_gained, d.days_since_prev
            FROM channel_daily d JOIN channels c USING (channel_id)""")
        made.append("ask_creator_history")

    if have("topic_current"):
        con.execute("""CREATE OR REPLACE VIEW ask_topic_attention AS
            SELECT tc.d AS day, t.name AS topic, tc.code, tc.level, t.sector, t.path,
                   tc.a AS attention_today, tc.a7 AS attention_7d, tc.a28 AS attention_28d, tc.wow AS week_over_week,
                   tc.acceleration, tc.share AS share_of_parent, tc.share_change_28d, tc.dss AS demand_signal_score,
                   tc.active_creators, tc.new_creator_share, tc.shorts_share, tc.advancers, tc.decliners, tc.thin
            FROM topic_current tc LEFT JOIN ask_topics t ON t.code = tc.code""")
        made.append("ask_topic_attention")

    if have("news"):
        src = "news_ranked" if have("news_ranked") else "news"
        score = "score" if src == "news_ranked" else "NULL AS score"
        extra = {c for (c,) in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'news'").fetchall()}
        pick = lambda c: c if c in extra else f"NULL AS {c}"
        con.execute(f"""CREATE OR REPLACE VIEW ask_news AS
            SELECT published_at, title, source, category, platform, impact, sentiment, creators,
                   {pick('label')}, {pick('status')}, {pick('note')}, summary, url, {score}
            FROM {src}""")
        made.append("ask_news")

    if have("creator_alerts"):
        con.execute("""CREATE OR REPLACE VIEW ask_tier_changes AS
            SELECT d AS day, title AS creator, channel_id, prev_tier AS from_tier, tier AS to_tier, cvs FROM creator_alerts""")
        made.append("ask_tier_changes")

    if have("creator_companies"):
        g = ("LEFT JOIN screener s USING (channel_id)" if have("screener") else "")
        growth = "s.subscribers, s.view_growth_28d, s.growth_vs_niche" if g else "NULL AS subscribers, NULL AS view_growth_28d, NULL AS growth_vs_niche"
        con.execute(f"""CREATE OR REPLACE VIEW ask_creator_companies AS
            SELECT c.creator, c.channel_id, c.in_terminal, c.stage, c.stage_name, c.main_company, c.outside_money,
                   c.raised_usd_m, c.research_status, c.confidence, c.source, c.notes, c.last_checked, {growth}
            FROM creator_companies c {g}""")
        made.append("ask_creator_companies")
    if have(*surface.REQUIRED):
        con.execute(f"""CREATE OR REPLACE VIEW ask_niche_skew AS
            SELECT t.name AS niche, k.niche_code, t.sector, k.videos, k.creators, k.small_reach AS small_creator_reach,
                   k.mid_reach AS median_creator_reach, k.big_reach AS big_creator_reach, k.skew, k.dispersion,
                   k.term AS reach_3m_vs_first_week
            FROM ({surface.SKEW_SQL}) k LEFT JOIN ask_topics t ON t.code = k.niche_code""")
        made.append("ask_niche_skew")

    if have("niche_rpm"):
        con.execute("""CREATE OR REPLACE VIEW ask_niche_rpm AS
            SELECT r.as_of, t.name AS niche, r.niche_code, t.sector, r.channels, r.rpm_low, r.rpm_mid, r.rpm_high, r.source
            FROM niche_rpm r LEFT JOIN ask_topics t ON t.code = r.niche_code
            WHERE r.as_of = (SELECT max(as_of) FROM niche_rpm)""")
        made.append("ask_niche_rpm")

    if have("creator_rounds"):
        con.execute("""CREATE OR REPLACE VIEW ask_creator_rounds AS
            SELECT round_date, creator, company, round, amount_m, valuation_m, currency, lead_investors, other_investors,
                   confidence, source FROM creator_rounds""")
        made.append("ask_creator_rounds")

    con.execute("CREATE OR REPLACE TABLE ask_guide (view_name VARCHAR, what_it_holds VARCHAR, how_to_use VARCHAR)")
    con.executemany("INSERT INTO ask_guide VALUES (?, ?, ?)", [g for g in GUIDE if g[0] in made] + [
        ("ask_guide", "Start here: the views built for asking questions in plain English.",
         "Prefer these ask_ views over the working tables; they use names instead of codes. Data is daily; the latest day is max(day) in ask_creator_history.")])
    made.append("ask_guide")

    # comments travel with the views, so the connector can read what each one and each key column means
    for name, what, how in GUIDE + [("ask_guide", "Start here: the views built for asking questions.", "")]:
        if name in made:
            kind = "VIEW" if name not in ("ask_topics", "ask_glossary", "ask_guide") else "TABLE"
            con.execute(f"COMMENT ON {kind} {name} IS ?".replace("?", "'" + (what + " " + how).replace("'", "''") + "'"))
    for view, cols in COMMENTS.items():
        if view in made:
            for c, text in cols.items():
                con.execute(f"COMMENT ON COLUMN {view}.{c} IS '" + text.replace("'", "''") + "'")
    return made
