"""Command line: python -m terminal <command>

  seed      add channels from config/channels.csv (ids or @handles)
  pull      today's snapshot of every channel and its recent videos
  classify  assign CTNCS codes to channels that don't have one
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

from . import classify, metrics, news, store, youtube

ROOT = Path(__file__).resolve().parent.parent


def _config() -> dict:
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())


def seed(con, path=ROOT / "config" / "channels.csv"):
    ids = []
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
    n = youtube.pull_channels(con, ids, dt.date.today())
    print(f"seeded {n} channels")


def pull(con):
    today = dt.date.today()
    ids = [c for (c,) in con.execute("SELECT channel_id FROM channels").fetchall()]
    print(f"channels: {youtube.pull_channels(con, ids, today)}")
    print(f"videos:   {youtube.pull_recent_videos(con, today, _config().get('videos_per_channel', 50))}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="terminal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["seed", "pull", "classify", "audit", "news", "compute", "export", "daily"])
    p.add_argument("--limit", type=int, help="classify at most this many channels")
    p.add_argument("--redo", action="store_true", help="reclassify channels nobody has reviewed yet")
    a = p.parse_args(argv)

    con = store.connect()
    news.connect(con)
    sectors = [str(s).zfill(2) for s in _config()["sectors"]]

    if a.command == "seed":
        seed(con)
    elif a.command == "pull":
        pull(con)
    elif a.command == "classify":
        print(f"classified {classify.classify_channels(con, sectors, a.limit, a.redo)}")
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
        print(f"articles: {news.pull(con)}, tagged: {news.tag(con)}")
        metrics.build(con)
        print("\n".join(metrics.export(con)))
