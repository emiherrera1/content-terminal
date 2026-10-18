"""Discovery: build the seed list from recent videos that are getting views in each target niche.

For each niche query, search recent videos ordered by views, collect the channels behind them,
then keep channels that are mid-size and active. Results merge into config/channels_discovered.csv,
so the job can run over several days without losing earlier finds.
"""
import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

import yaml

from . import youtube

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "channels_discovered.csv"
FIELDS = ["channel", "niche", "title", "subscribers", "videos", "hits", "first_found", "queries"]
SEARCH_COST = 100


def load_config(path=ROOT / "config" / "discovery.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text())


def load_existing(path=OUT) -> dict:
    if not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["channel"]: r for r in csv.DictReader(f)}


def search_hits(cfg: dict, search=None) -> tuple[dict, int]:
    """Run every query. Returns ({niche: {channel_id: [queries]}}, units used)."""
    search = search or youtube.search_recent_videos
    after = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cfg.get("window_days", 60))
    hits = defaultdict(lambda: defaultdict(list))
    units = 0
    for niche, queries in cfg["niches"].items():
        for q in queries:
            token = None
            for _ in range(cfg.get("pages_per_query", 1)):
                if units + SEARCH_COST > cfg.get("max_units", 6000):
                    print(f"stopped at {units} units (max_units)")
                    return hits, units
                channel_ids, token = search(q, after, cfg.get("region"), cfg.get("language"), token)
                units += SEARCH_COST
                for cid in channel_ids:
                    hits[str(niche)][cid].append(q)
                if not token:
                    break
    return hits, units


def select(hits: dict, stats: dict, cfg: dict, existing: dict, screened: dict | None = None) -> list[dict]:
    """Filter by size and activity, give each channel to the niche where it showed up most,
    and keep the top `target_per_niche` per niche (existing finds count toward the target)."""
    f = cfg.get("filters", {})
    screened = screened or {}
    best = {}
    for niche, chans in hits.items():
        for cid, qs in chans.items():
            if cid not in best or len(qs) > len(best[cid][1]):
                best[cid] = (niche, qs)

    # only channels still in play count toward the per-niche target, so screened-out ones free their slot
    dropped = {cid for cid, r in screened.items() if r.get("verdict") == "drop"}
    per_niche = defaultdict(int)
    for cid, row in existing.items():
        if cid not in dropped:
            per_niche[row["niche"]] += 1
    allowed = {c.upper() for c in f.get("countries", [])}

    candidates = []
    for cid, (niche, qs) in best.items():
        if cid in existing:
            continue
        s = stats.get(cid)
        if not s or s["hidden"]:
            continue
        if not (f.get("min_subscribers", 0) <= s["subscribers"] <= f.get("max_subscribers", 10**12)):
            continue
        if s["videos"] < f.get("min_videos", 0):
            continue
        if allowed and s.get("country") and s["country"].upper() not in allowed:
            continue   # channel says it is based elsewhere (unset country passes; screening checks it)
        candidates.append({"channel": cid, "niche": niche, "title": s["title"], "subscribers": s["subscribers"],
                           "videos": s["videos"], "hits": len(qs), "first_found": dt.date.today().isoformat(),
                           "queries": "; ".join(sorted(set(qs)))})

    candidates.sort(key=lambda r: (r["niche"], -r["hits"], r["subscribers"]))
    out = []
    for r in candidates:
        if per_niche[r["niche"]] < cfg.get("target_per_niche", 10**9):
            out.append(r)
            per_niche[r["niche"]] += 1
    return out


def write(rows: list[dict], existing: dict, path=OUT) -> int:
    merged = list(existing.values()) + rows
    merged.sort(key=lambda r: (r["niche"], -int(r["hits"]), int(r["subscribers"])))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in merged)
    return len(merged)


def run(cfg=None, search=None, stats_fn=None, path=OUT) -> dict:
    cfg = cfg or load_config()
    existing = load_existing(path)
    hits, units = search_hits(cfg, search)
    ids = sorted({cid for chans in hits.values() for cid in chans})
    stats = (stats_fn or youtube.channel_stats)(ids)
    units += -(-len(ids) // 50)  # channels.list: 1 unit per 50 channels
    from . import screen
    new = select(hits, stats, cfg, existing, screen.load())
    total = write(new, existing, path)
    by_niche = defaultdict(int)
    for r in load_existing(path).values():
        by_niche[r["niche"]] += 1
    return {"units": units, "channels_seen": len(ids), "added": len(new), "total": total, "by_niche": dict(by_niche)}
