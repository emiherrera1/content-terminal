"""Every outside input the terminal reads besides the daily YouTube pull, behind one switch each.

Each source reports whether it is configured; the pipeline runs the configured ones and skips the rest with a
note, so switching a source on is a matter of adding a key or a file, never new code.

  comments          6.3  YouTube commentThreads for a watchlist of topics (config/formulas.yaml `comments`)
  google_trends     6.6-6.7, 8.x outcome series: weekly search interest per niche (config/outcomes.yaml)
  outcome_csv       6.6-6.7, 8.x any other outcome series as CSV in data/inputs/outcomes/*.csv
                         columns: series_id, code, label, d (week start), value
  outside_estimates 10.4 data/inputs/outside_estimates.csv: code, period_start, period_end, total_views, source
  tiktok, instagram 9.x  adapters for other platforms; see the notes on each class for what access each needs
"""
import csv
import datetime as dt
import os
from pathlib import Path

import yaml

from . import formulas, tagging

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "data" / "inputs"
OUTCOMES_CFG = ROOT / "config" / "outcomes.yaml"


class NotConfigured(Exception):
    """The source has no credentials or input files yet. The pipeline records it as skipped."""


# ---------------------------------------------------------------- 6.3 comments
def pull_comments(con, today: dt.date | None = None, cfg: dict | None = None) -> int:
    c = (cfg or formulas.config()).get("comments", {})
    if not c.get("enabled") or not c.get("topics"):
        raise NotConfigured("comments are off (config/formulas.yaml comments.enabled and comments.topics)")
    from . import youtube
    today = today or dt.date.today()
    codes = list(c["topics"])
    marks = ", ".join("?" for _ in codes)
    vids = [r[0] for r in con.execute(f"""
        SELECT DISTINCT m.video_id FROM topic_map m JOIN video_metrics v USING (video_id)
        WHERE m.code IN ({marks}) AND m.video_id NOT IN (SELECT DISTINCT video_id FROM video_comments)
        ORDER BY v.views DESC LIMIT ?""", codes + [int(c.get("max_videos_per_day", 50))]).fetchall()]
    n = 0
    for vid in vids:
        try:
            data = youtube._get("commentThreads", part="snippet", videoId=vid, maxResults=min(int(c.get("per_video", 100)), 100),
                                order="relevance", textFormat="plainText")
        except Exception:
            continue    # comments disabled on the video
        for item in data.get("items", []):
            top = item["snippet"]["topLevelComment"]
            text = top["snippet"].get("textDisplay", "")
            con.execute("INSERT OR REPLACE INTO video_comments VALUES (?, ?, ?, ?, ?, 'rules', ?)",
                        [top["id"], vid, top["snippet"].get("publishedAt"), text[:2000], tagging.purchase_intent(text), today])
            n += 1
    return n


# ---------------------------------------------------------------- outcome series
def outcome_config(path=OUTCOMES_CFG) -> dict:
    return yaml.safe_load(Path(path).read_text()) if Path(path).exists() else {}


def pull_google_trends(con, cfg: dict | None = None) -> int:
    """Weekly Google search interest for each niche's terms (pytrends). Google Trends values are relative (0-100)
    within one request, so each term is pulled on its own and stored as its own series."""
    cfg = cfg if cfg is not None else outcome_config()
    series = [s for s in cfg.get("google_trends", []) if s.get("terms")]
    if not series:
        raise NotConfigured("no Google Trends terms chosen yet (config/outcomes.yaml)")
    try:
        from pytrends.request import TrendReq
    except ImportError as e:
        raise NotConfigured("pytrends is not installed") from e
    py = TrendReq(hl="en-US", tz=300)
    n = 0
    for s in series:
        for term in s["terms"]:
            py.build_payload([term], timeframe=s.get("timeframe", "today 5-y"), geo=s.get("geo", "US"))
            df = py.interest_over_time()
            if df is None or df.empty:
                continue
            sid = f"trends:{s['code']}:{s.get('geo', 'US')}:{term}"
            for d, v in df[term].items():
                week = (d.date() - dt.timedelta(days=d.weekday()))
                con.execute("INSERT OR REPLACE INTO outcome_series VALUES (?, 'google_trends', ?, ?, ?, ?)",
                            [sid, s["code"], f"Google Trends: {term} ({s.get('geo', 'US')})", week, float(v)])
                n += 1
    return n


def load_outcome_csvs(con, folder: Path = INPUTS / "outcomes") -> int:
    files = sorted(Path(folder).glob("*.csv"))
    if not files:
        raise NotConfigured(f"no outcome files in {folder}")
    n = 0
    for f in files:
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                d = dt.date.fromisoformat(r["d"])
                week = d - dt.timedelta(days=d.weekday())
                con.execute("INSERT OR REPLACE INTO outcome_series VALUES (?, ?, ?, ?, ?, ?)",
                            [r["series_id"], r.get("source") or "csv", r["code"], r.get("label") or r["series_id"], week, float(r["value"])])
                n += 1
    return n


def load_outside_estimates(con, path: Path = INPUTS / "outside_estimates.csv") -> int:
    if not Path(path).exists():
        raise NotConfigured(f"no outside estimates at {path}")
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            con.execute("INSERT OR REPLACE INTO outside_estimates VALUES (?, ?, ?, ?, ?)",
                        [r["code"], r["period_start"], r["period_end"], float(r["total_views"]), r.get("source", "")])
            n += 1
    return n


# ---------------------------------------------------------------- other platforms (9.x)
class PlatformAdapter:
    """Writes another platform's accounts and posts into the same tables YouTube uses, with ids prefixed
    (tt:, ig:) and channels.platform set, so every formula runs on them unchanged.

    pull(con, today) must: upsert channels (channel_id, title, platform, published_at), write channel_snapshots
    (followers in `subscribers`, total views or plays in `views`), upsert recent posts into videos
    (video_id, channel_id, title, published_at, duration_s) and write video_snapshots (views or plays)."""
    platform = ""
    prefix = ""
    env: tuple[str, ...] = ()

    def configured(self) -> bool:
        return all(os.environ.get(k) for k in self.env)

    def accounts(self, con) -> list[str]:
        path = ROOT / "config" / f"channels_{self.platform}.csv"
        if not path.exists():
            return []
        with open(path, newline="", encoding="utf-8") as f:
            return [r["handle"].strip() for r in csv.DictReader(f) if (r.get("handle") or "").strip()]

    def pull(self, con, today: dt.date) -> int:
        if not self.configured():
            raise NotConfigured(f"{self.platform}: set {', '.join(self.env)}")
        if not self.accounts(con):
            raise NotConfigured(f"{self.platform}: no accounts in config/channels_{self.platform}.csv")
        return self.fetch(con, today)

    def fetch(self, con, today: dt.date) -> int:
        raise NotConfigured(f"{self.platform}: adapter written, API client not connected yet")


class TikTok(PlatformAdapter):
    """TikTok Research API (developers.tiktok.com/products/research-api). Access is by application and is aimed at
    non-profit researchers, which C-CPAR may qualify for. Gives user info (follower count) and video lists with
    view, like and comment counts."""
    platform, prefix, env = "tiktok", "tt:", ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET")


class Instagram(PlatformAdapter):
    """Instagram Graph API business discovery: public metrics for Business and Creator accounts, read through a
    Facebook app and an Instagram professional account of the fund's own. Reels plays are limited; check the
    current fields before relying on them."""
    platform, prefix, env = "instagram", "ig:", ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_BUSINESS_ID")


PLATFORMS = [TikTok(), Instagram()]
