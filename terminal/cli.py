"""Command line: python -m terminal <command>

  discover  find active mid-size channels per niche (config/discovery.yaml) -> config/channels_discovered.csv
  seed      add channels from config/channels.csv and config/channels_discovered.csv
  pull      today's snapshot of every channel and its recent videos
  classify  assign CTNCS codes to channels that don't have one
  classify-videos  assign each recent video its own topic (to sub-niche) and format
  index     how a topic did on a day, from video-level data: python -m terminal index woodworking [--date YYYY-MM-DD]
  audit     print a random sample of classifications to check by hand
  compute   rebuild the metric views
  export    write niche index, momentum, screener and alerts to CSV
  news      pull news feeds and tag new articles
  daily     pull + classify new channels + news + compute + export (what the schedule runs)
"""
import argparse
import csv
import datetime as dt
from pathlib import Path

import yaml

from . import classify, content_index, ctncs, discover, metrics, news, store, video_classify, youtube

ROOT = Path(__file__).resolve().parent.parent


def _config() -> dict:
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())


def seed(con, paths=(ROOT / "config" / "channels.csv", ROOT / "config" / "channels_discovered.csv")):
    ids = []
    for path in paths:
        if not Path(path).exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ref = (row.get("channel") or "").strip()
                if not ref or ref.startswith("#"):
                    continue
                cid = ref if ref.startswith("UC") else youtube.resolve_handle(ref)
                if cid:
                    ids.append(cid)
                else:
                    print(f"not found: {ref}")
    ids = list(dict.fromkeys(ids))
    n = youtube.pull_channels(con, ids, dt.date.today())
    print(f"seeded {n} channels")


def pull(con):
    today = dt.date.today()
    ids = [c for (c,) in con.execute("SELECT channel_id FROM channels").fetchall()]
    print(f"channels: {youtube.pull_channels(con, ids, today)}")
    print(f"videos:   {youtube.pull_recent_videos(con, today, _config().get('videos_per_channel', 50))}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="terminal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["discover", "seed", "pull", "classify", "classify-videos", "index", "audit", "news", "compute", "export", "daily"])
    p.add_argument("--limit", type=int, help="classify at most this many channels")
    p.add_argument("--redo", action="store_true", help="reclassify channels nobody has reviewed yet")
    p.add_argument("query", nargs="*", help="index: a topic name or CTNCS code, e.g. woodworking or 02.05.10.19")
    p.add_argument("--date", help="index: the day to report (default: latest)")
    a = p.parse_args(argv)

    con = store.connect()
    news.connect(con)
    sectors = [str(s).zfill(2) for s in _config()["sectors"]]

    if a.command == "discover":
        r = discover.run()
        print(f"units used: {r['units']}, channels seen: {r['channels_seen']}, added: {r['added']}, total: {r['total']}")
        for niche, n in sorted(r["by_niche"].items()):
            print(f"  {niche}: {n}")
    elif a.command == "seed":
        seed(con)
    elif a.command == "pull":
        pull(con)
    elif a.command == "classify":
        print(f"classified {classify.classify_channels(con, sectors, a.limit, a.redo)}")
    elif a.command == "classify-videos":
        print(f"classified videos: {video_classify.classify_videos(con, a.limit)}")
    elif a.command == "index":
        metrics.build(con)
        hits = ctncs.resolve(ctncs.load(), " ".join(a.query)) if a.query else []
        if not hits:
            raise SystemExit("No CTNCS topic matches that name. Try a code (02.05.10.19) or part of a label.")
        if len(hits) > 1:
            print("Several topics match; showing the first. Others: " + ", ".join(f"{h['label']} ({h['code']})" for h in hits[1:6]))
        day = dt.date.fromisoformat(a.date) if a.date else None
        print(content_index.render(content_index.report(con, hits[0]["code"], day)))
    elif a.command == "audit":
        for row in classify.audit_sample(con):
            print(" | ".join(str(x) for x in row))
    elif a.command == "news":
        print(f"articles: {news.pull(con)}, tagged: {news.tag(con, a.limit)}")
    elif a.command == "compute":
        metrics.build(con)
    elif a.command == "export":
        metrics.build(con)
        print("\n".join(metrics.export(con)))
    elif a.command == "daily":
        pull(con)
        print(f"classified {classify.classify_channels(con, sectors)}")
        print(f"classified videos: {video_classify.classify_videos(con, _config().get('video_classify_daily_limit'))}")
        print(f"articles: {news.pull(con)}, tagged: {news.tag(con)}")
        metrics.build(con)
        print("\n".join(metrics.export(con)))
