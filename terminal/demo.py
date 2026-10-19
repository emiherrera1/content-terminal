"""A synthetic world for running every formula end to end before real data exists.

    python -m terminal demo            builds data/demo.duckdb and writes data/exports_demo/

Nothing here touches the real database, the real call log or the real exports. Every table the demo writes lives in
data/demo.duckdb, its calls in data/demo/, and its exports in data/exports_demo/ with a SYNTHETIC notice.

The world is built so the right answers are known in advance, which lets the tests check that the formulas find them:
  - each niche has a hidden demand curve; video views follow it, and the niche's outcome series (a stand-in for
    Google Trends) follows the same curve LEADS[niche] weeks later, so 6.6 should recover those leads
  - YouTube, TikTok and Instagram creators share the curve, with TikTok PLATFORM_LAG weeks ahead (9.4)
  - subscriber counts are rounded to 3 significant figures, as YouTube shows them (1.8)
  - a fixed share of creators drift into a second niche halfway through (3.19, 5.8)
"""
import csv
import datetime as dt
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from . import analytics, calls, ctncs, formulas, metrics, store, tagging

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "demo.duckdb"
DEMO_DIR = ROOT / "data" / "demo"
EXPORTS = ROOT / "data" / "exports_demo"

NICHES = ["03.07", "03.08", "03.09", "04.10", "08.23", "08.25", "09.26", "09.27"]
LEADS = {"03.07": 4, "03.08": 6, "03.09": 3, "04.10": 5, "08.23": 8, "08.25": 4, "09.26": 6, "09.27": 7}
PLATFORM_LAG = 2          # TikTok attention leads YouTube by this many weeks
DAYS = 196                # 28 weeks of daily snapshots
HISTORY_WEEKS = 120       # outcome series run back further than the snapshots, as Google Trends does

WORDS = {"03.07": ["home gym", "pilates", "leg day", "creatine"], "03.08": ["air fryer", "meal prep", "sourdough", "cottage cheese"],
         "03.09": ["skincare", "lip oil", "capsule wardrobe", "fall outfits"], "04.10": ["smart ring", "keyboard", "earbuds", "handheld PC"],
         "08.23": ["side hustle", "LLC", "first 10 customers", "Shopify store"], "08.25": ["Roth IRA", "budget", "high yield savings", "credit score"],
         "09.26": ["protein", "electrolytes", "sleep", "GLP-1"], "09.27": ["anxiety", "journaling", "therapy", "burnout"]}
TEMPLATES = ["A day in my life: {w}", "I tried {w} for 30 days", "{w} tier list", "{b} vs {w}", "Honest review: {b}",
             "My {w} haul", "{w} mistakes you're making", "How to start {w}", "Is {b} worth it?", "{w} on a $50 budget",
             "My morning routine ({w})", "Before and after: {w}", "{w} explained", "Why everyone is talking about {w}"]
FORMATS = ["Tutorials", "Reviews", "Haul", "Comparison", "Vlog", "Commentary", "DIY", "Recipes", "Explainer", "Challenge"]
INTENT_PHRASES = ["where can I buy this", "link?", "how much was it", "is this available in Canada", "need this",
                  "what's the discount code"]
PLAIN_PHRASES = ["love this", "great video", "so helpful", "first!", "you're the best", "watching from London"]


def _topics():
    book = ctncs.load()
    out = {}
    for cat in NICHES:
        l3 = [c for c, r in book.items() if c.startswith(cat + ".") and r["level"] == "3"][:3]
        out[cat] = {n: [c for c, r in book.items() if c.startswith(n + ".") and r["level"] == "4"][:2] for n in l3}
    return out


def _format_ids():
    by_label = {v: k for k, v in ctncs.formats().items()}
    return {f: by_label[f] for f in FORMATS if f in by_label}


def _brands():
    out = {}
    with open(ROOT / "config" / "crosswalk.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            names = [n.split("(")[0].strip() for n in (r["public_companies"] + ";" + r["private_brands"]).split(";")]
            out[r["code"]] = [n for n in names if n]
    return out


def demand_curves(rng, end: dt.date):
    """Weekly log-demand per niche: a random walk with drift and a seasonal wobble, HISTORY_WEEKS long."""
    weeks = pd.date_range(end=pd.Timestamp(end) - pd.Timedelta(days=pd.Timestamp(end).weekday()) + pd.Timedelta(weeks=4), periods=HISTORY_WEEKS + 4, freq="7D")
    curves = {}
    for i, n in enumerate(NICHES):
        drift = rng.normal(0.004, 0.006)
        steps = rng.normal(drift, 0.15, len(weeks))
        season = 0.08 * np.sin(np.arange(len(weeks)) * 2 * math.pi / 26 + i)
        curves[n] = pd.Series(np.cumsum(steps) + season, index=weeks)
    return curves


def _daily_factor(curve: pd.Series, day: dt.date, lag_weeks: float = 0) -> float:
    t = pd.Timestamp(day) + pd.Timedelta(weeks=lag_weeks)
    x = (curve.index - curve.index[0]) / pd.Timedelta(days=1)      # unit-safe (pandas 3 may store microseconds)
    return float(math.exp(np.interp((t - curve.index[0]) / pd.Timedelta(days=1), x, curve.values)))


def build(con, end: dt.date | None = None, seed: int = 7, n_youtube: int = 72, n_tiktok: int = 16, n_instagram: int = 12) -> dict:
    rng = np.random.default_rng(seed)
    end = end or dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=DAYS - 1)
    topics, fmt_ids, brands = _topics(), _format_ids(), _brands()
    curves = demand_curves(rng, end)

    # ---- channels
    chans = []
    plats = [("youtube", "UC", n_youtube), ("tiktok", "tt:", n_tiktok), ("instagram", "ig:", n_instagram)]
    for plat, prefix, n in plats:
        for i in range(n):
            niche = NICHES[i % len(NICHES)]
            l3 = list(topics[niche])[i % len(topics[niche])]
            size = float(np.exp(rng.normal(10.2, 0.5)))                       # typical daily views per video at peak
            young = rng.random() < 0.15
            chans.append({
                "channel_id": f"{prefix}demo{plat[:2]}{i:03d}", "title": f"Demo {plat.title()} Creator {i + 1}", "platform": plat,
                "niche": niche, "l3": l3, "size": size, "every": int(rng.integers(3, 8)),
                "created": start - dt.timedelta(days=int(rng.integers(20, 60))) if young else start - dt.timedelta(days=int(rng.integers(400, 3000))),
                "added": start if rng.random() > 0.1 else start + dt.timedelta(days=int(rng.integers(30, 90))),
                "drifts": rng.random() < 0.12, "country": rng.choice(["US", "US", "US", "CA", "GB", "AU"])})
    con.executemany("INSERT INTO channels (channel_id, title, description, country, published_at, uploads_playlist, added_at, platform) "
                    "VALUES (?, ?, '', ?, ?, ?, ?, ?)",
                    [(c["channel_id"], c["title"], c["country"], dt.datetime.combine(c["created"], dt.time(9)),
                      "UU" + c["channel_id"][2:] if c["platform"] == "youtube" else None, dt.datetime.combine(c["added"], dt.time(6)),
                      c["platform"]) for c in chans])
    con.executemany("INSERT INTO classifications (channel_id, ctncs_code, sector, category, niche, confidence, rationale, model) "
                    "VALUES (?, ?, ?, ?, ?, 0.9, 'synthetic', 'demo')",
                    [(c["channel_id"], c["l3"], c["niche"][:2], c["niche"], c["l3"]) for c in chans])

    # ---- videos and daily snapshots. factor(niche, ahead)[i] = demand on day start + (i + lo), read `ahead` weeks early
    lo = -210
    cache = {}

    def factor(niche, ahead):
        if (niche, ahead) not in cache:
            cache[(niche, ahead)] = np.array([_daily_factor(curves[niche], start + dt.timedelta(days=i + lo), ahead)
                                              for i in range(DAYS - lo)])
        return cache[(niche, ahead)]

    vids, vsnap, vcls = [], [], []
    for c in chans:
        ahead = PLATFORM_LAG if c["platform"] == "tiktok" else 0
        other = NICHES[(NICHES.index(c["niche"]) + 3) % len(NICHES)]
        first = -int(rng.integers(30, 200))   # >= lo                                     # a few uploads before tracking began
        k = 0
        for pub in range(first, DAYS, c["every"]):
            pub_day = start + dt.timedelta(days=pub)
            niche = other if c["drifts"] and pub > DAYS // 2 and rng.random() < 0.6 else c["niche"]
            l3 = rng.choice(list(topics[niche])) if niche != c["niche"] else c["l3"]
            l4s = topics[niche][l3]
            topic = rng.choice(l4s) if l4s and rng.random() < 0.7 else l3
            fmt = rng.choice(FORMATS)
            word, brand = rng.choice(WORDS[niche]), rng.choice(brands.get(niche, ["it"]))
            title = rng.choice(TEMPLATES).format(w=word, b=brand)
            short = c["platform"] != "youtube" or rng.random() < 0.25
            dur = int(rng.integers(15, 90)) if short else int(rng.integers(360, 1800))
            vid = f"{c['channel_id'].replace(':', '_')}v{k:04d}"
            k += 1
            boost = float(np.exp(rng.normal(0, 0.2))) * (5.0 if rng.random() < 0.02 else 1.0)
            lam = float(rng.uniform(0.08, 0.35))
            tail = float(rng.uniform(0.01, 0.05))
            vids.append((vid, c["channel_id"], title, dt.datetime.combine(pub_day, dt.time(15)), dur,
                         f"Today: {word}. Thanks to {brand}.", f"{word}, {niche}"))
            parts = topic.split(".")
            vcls.append((vid, topic, parts[0], ".".join(parts[:2]), ".".join(parts[:3]), topic if len(parts) == 4 else None,
                         fmt_ids.get(fmt), fmt, float(rng.uniform(0.55, 0.99)), "demo"))
            ds = np.arange(pub, DAYS)
            rate = c["size"] * boost * (np.exp(-lam * (ds - pub)) + tail) * factor(niche, ahead)[ds - lo]
            total = np.cumsum(rate * rng.uniform(0.85, 1.15, len(ds)))
            keep = ds >= 0
            vsnap.append(pd.DataFrame({"snapshot_date": [start + dt.timedelta(days=int(d)) for d in ds[keep]], "video_id": vid,
                                       "views": total[keep].astype(np.int64), "likes": (total[keep] * 0.04).astype(np.int64),
                                       "comments": (total[keep] * 0.004).astype(np.int64)}))
    con.executemany("INSERT INTO videos (video_id, channel_id, title, published_at, duration_s, description, tags) VALUES (?, ?, ?, ?, ?, ?, ?)", vids)
    con.executemany("INSERT INTO video_classifications (video_id, topic_code, sector, category, niche, subniche, format_id, "
                    "format_label, confidence, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", vcls)
    vsnap = pd.concat(vsnap, ignore_index=True)
    con.register("_vs", vsnap)
    con.execute("INSERT INTO video_snapshots SELECT * FROM _vs")
    con.unregister("_vs")

    # ---- channel snapshots: views = tracked views + a back catalog; subscribers rounded to 3 significant figures
    tracked = con.execute("""SELECT s.snapshot_date, v.channel_id, sum(s.views) FROM video_snapshots s JOIN videos v USING (video_id)
                             GROUP BY ALL""").fetchall()
    by = {}
    for d, ch, v in tracked:
        by.setdefault(ch, {})[d] = v
    csnap = []
    for c in chans:
        subs0 = c["size"] * float(rng.uniform(15, 40))
        back = c["size"] * 400
        for d in range(DAYS):
            day = start + dt.timedelta(days=d)
            if rng.random() < 0.01:
                continue                                                      # a missed pull now and then (10.1)
            subs = subs0 * (1 + 0.0015 * d)
            subs = int(round(subs, -max(len(str(int(subs))) - 3, 0)))
            csnap.append((day, c["channel_id"], subs, int(back * (1 + 0.001 * d) + by.get(c["channel_id"], {}).get(day, 0)), 0))
    con.executemany("INSERT INTO channel_snapshots VALUES (?, ?, ?, ?, ?)", csnap)

    # ---- comments (6.3), outcome series (6.6), outside estimates (10.4), an audit (10.3)
    review_ids = [v[0] for v in vids if "review" in v[2].lower() or "worth it" in v[2].lower()][:60]
    plain_ids = [v[0] for v in vids][:60]
    com = []
    for vid in set(review_ids + plain_ids):
        rate = 0.25 if vid in review_ids else 0.05
        for j in range(20):
            text = rng.choice(INTENT_PHRASES) if rng.random() < rate else rng.choice(PLAIN_PHRASES)
            com.append((f"{vid}c{j}", vid, None, text, tagging.purchase_intent(text), "rules", end))
    con.executemany("INSERT INTO video_comments VALUES (?, ?, ?, ?, ?, ?, ?)", com)

    out = []
    for n in NICHES:
        for w in curves[n].index[curves[n].index <= pd.Timestamp(end)]:
            y = 50 * math.exp(curves[n].get(w - pd.Timedelta(weeks=LEADS[n]), curves[n].iloc[0]) + rng.normal(0, 0.03))
            out.append((f"synthetic:{n}", "synthetic", n, f"Synthetic outcome for {n}", w.date(), y))
    con.executemany("INSERT INTO outcome_series VALUES (?, ?, ?, ?, ?, ?)", out)

    con.execute(metrics.VIEWS)
    est = con.execute("""SELECT c.category, min(d.snapshot_date), max(d.snapshot_date), sum(d.views_gained) / 0.15
                         FROM video_daily d JOIN video_classifications c USING (video_id)
                         WHERE d.views_gained IS NOT NULL GROUP BY c.category""").fetchall()
    con.executemany("INSERT INTO outside_estimates VALUES (?, ?, ?, ?, 'synthetic')", est)

    sample = con.execute("SELECT video_id, topic_code FROM video_classifications ORDER BY video_id LIMIT 25").fetchall()
    con.executemany("INSERT INTO classification_audits VALUES (?, 'video', ?, ?, ?, ?, ?, 'demo')",
                    [(end, vid, code.split(".")[0], code, code if i % 6 else "03.08.015", bool(i % 6)) for i, (vid, code) in enumerate(sample)])
    con.execute("UPDATE video_classifications SET reviewed = true WHERE video_id IN (SELECT item_id FROM classification_audits)")

    return {"start": start, "end": end, "channels": len(chans), "videos": len(vids), "video_snapshots": len(vsnap),
            "curves": curves}


def write_calls(con, world: dict, folder: Path = DEMO_DIR) -> tuple[Path, Path]:
    """Back-dated calls on each niche, made at weeks 6-14, using the demo outcome series."""
    folder.mkdir(parents=True, exist_ok=True)
    cpath, rpath = folder / "calls.csv", folder / "resolutions.csv"
    for p in (cpath, rpath):
        p.unlink(missing_ok=True)
    rng = np.random.default_rng(11)
    i = 0
    for n in NICHES:
        for wk in (6, 10, 14):
            made = world["start"] + dt.timedelta(weeks=wk)
            curve = world["curves"][n]
            # call the direction the hidden curve has moved over the past 4 weeks (what the terminal would have seen)
            past = _daily_factor(curve, made) / _daily_factor(curve, made - dt.timedelta(weeks=4))
            i += 1
            row = {"id": i, "made_at": dt.datetime.combine(made, dt.time(14)).isoformat(), "subject_type": "topic", "subject": n,
                   "label": n, "signal": "dss", "score": "", "direction": "up" if past >= 1 else "down", "horizon_weeks": 10,
                   "outcome_series": f"synthetic:{n}", "probability": round(float(rng.uniform(0.55, 0.8)), 2), "made_by": "demo",
                   "note": "synthetic"}
            calls._append(cpath, calls.CALL_FIELDS, row)
    return cpath, rpath


def main(db: Path = DB, exports: Path = EXPORTS) -> dict:
    db.parent.mkdir(parents=True, exist_ok=True)
    for p in (db, Path(str(db) + ".wal")):
        p.unlink(missing_ok=True)
    con = store.connect(str(db))
    world = build(con)
    cpath, rpath = write_calls(con, world)
    from . import pipeline
    pipeline.run(con, "rebuild", today=world["end"], calls_paths=(cpath, rpath),
                 stages=[s for s in pipeline.JOBS["rebuild"] if s.name != "export"])
    if exports.exists():
        shutil.rmtree(exports)
    paths = metrics.export(con, str(exports))
    (exports / "README.txt").write_text("SYNTHETIC DATA. Generated by `python -m terminal demo` to exercise every formula. "
                                        "Nothing in this folder describes a real creator, topic or market.\n")
    summary = summarize(con)
    print(f"demo world: {world['channels']} channels, {world['videos']} videos, {world['video_snapshots']} video snapshots, "
          f"{world['start']} to {world['end']}")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"exports: {exports} ({len(paths)} files)")
    return summary


def summarize(con) -> dict:
    best = dict(con.execute("SELECT code, lag_weeks FROM lead_lag WHERE is_best").fetchall())
    s = con.execute("SELECT * FROM call_summary").df().iloc[0].to_dict()
    return {
        "lead-lag recovered (found vs true weeks)": {n: (best.get(n), LEADS[n]) for n in NICHES},
        "predictive tests passed": con.execute("SELECT count(*) FILTER (WHERE passes), count(*) FROM predictive_tests").fetchone(),
        "track record": {k: (round(v, 3) if isinstance(v, float) and not math.isnan(v) else v) for k, v in s.items()},
        "platform lead (tiktok leads youtube, weeks)": con.execute(
            "SELECT round(median(best_lag_weeks)) FROM platform_lead WHERE leader = 'tiktok' AND follower = 'youtube'").fetchone()[0],
        "tables with rows": con.execute("""SELECT count(*) FROM duckdb_tables() WHERE estimated_size > 0""").fetchone()[0],
    }
