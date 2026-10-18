"""Command line: python -m terminal <command>

  discover  find active mid-size channels per niche (config/discovery.yaml) -> config/channels_discovered.csv
  seed      add channels from config/channels.csv and config/channels_discovered.csv
  pull      today's snapshot of every channel and its recent videos
  classify  assign CTNCS codes to channels that don't have one
  classify-videos  assign each recent video its own topic (to sub-niche) and format
  migrate-ctncs  rewrite stored CTNCS v1.0 codes to v2.0 (one-time)
  watchlist creators ranked by Creator Viability Score, with tiers
  review    record the manual gates for a creator: --creator UC... --independent yes --clean yes
  call      log a call before the outcome is known: --topic woodworking --direction up --outcome "..." [--p 0.6]
  resolve   record a call's outcome: --id 3 --outcome hit --note "<evidence>"
  calls     list calls and check the log hasn't been edited
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

from . import calls, classify, content_index, creators, ctncs, discover, metrics, news, store, video_classify, youtube

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
    p.add_argument("command", choices=["discover", "seed", "pull", "classify", "classify-videos", "index", "watchlist", "review",
                                      "call", "resolve", "calls", "migrate-ctncs", "audit", "news", "compute", "export", "daily"])
    p.add_argument("--limit", type=int, help="classify at most this many channels")
    p.add_argument("--redo", action="store_true", help="reclassify channels nobody has reviewed yet")
    p.add_argument("query", nargs="*", help="index: a topic name or CTNCS code, e.g. woodworking or 02.05.010.019")
    p.add_argument("--date", help="index: the day to report (default: latest)")
    p.add_argument("--topic", help="call: a CTNCS topic code or name")
    p.add_argument("--creator", help="call / review: a channel id")
    p.add_argument("--signal", default="dss", help="call: dss, index, wow, share, new_creator_share or cvs")
    p.add_argument("--direction", choices=["up", "down"], help="call: what you expect the outcome series to do")
    p.add_argument("--horizon", type=int, default=13, help="call: weeks until the call is judged (default 13)")
    p.add_argument("--outcome", help="call: the outcome series, e.g. 'Google Trends: woodworking, US'; resolve: hit, miss or void")
    p.add_argument("--p", type=float, help="call: your probability that it happens (0 to 1)")
    p.add_argument("--by", default="", help="call / review / resolve: who made it")
    p.add_argument("--note", default="", help="call / review / resolve: a note or the evidence")
    p.add_argument("--id", type=int, help="resolve: the call id")
    p.add_argument("--independent", choices=["yes", "no"], help="review: creator is independent (not network- or brand-owned)")
    p.add_argument("--clean", choices=["yes", "no"], help="review: no conduct or legal issues found")
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
            raise SystemExit("No CTNCS topic matches that name. Try a code (02.05.010.019) or part of a label.")
        if len(hits) > 1:
            print("Several topics match; showing the first. Others: " + ", ".join(f"{h['label']} ({h['code']})" for h in hits[1:6]))
        day = dt.date.fromisoformat(a.date) if a.date else None
        print(content_index.render(content_index.report(con, hits[0]["code"], day)))
    elif a.command == "watchlist":
        metrics.build(con)
        rows = con.execute("SELECT coalesce(tier, '-'), round(cvs, 1), title, niche, channel_id, needs_review "
                           "FROM creator_watchlist WHERE cvs IS NOT NULL ORDER BY cvs DESC LIMIT ?", [a.limit or 25]).fetchall()
        for tier, cvs, title, niche, cid, needs in rows:
            print(f"{tier:<16} {cvs:>5}  {title or cid:<36} {niche or '':<12}{'  needs manual review' if needs else ''}")
    elif a.command == "review":
        if not (a.creator and a.independent and a.clean):
            raise SystemExit("review needs --creator, --independent yes|no and --clean yes|no")
        creators.review(con, a.creator, a.independent == "yes", a.clean == "yes", a.by, a.note)
        print(f"recorded review for {a.creator}")
    elif a.command == "call":
        if not (a.direction and a.outcome and (a.topic or a.creator)):
            raise SystemExit("call needs --topic or --creator, --direction up|down and --outcome '<series>'")
        metrics.build(con)
        if a.topic:
            hit = ctncs.resolve(ctncs.load(), a.topic)
            if not hit:
                raise SystemExit(f"no CTNCS topic matches {a.topic}")
            subject, label, kind = hit[0]["code"], hit[0]["label"], "topic"
        else:
            subject, kind = a.creator, "creator"
            label = (con.execute("SELECT title FROM channels WHERE channel_id = ?", [subject]).fetchone() or [""])[0]
        row = calls.add(con, kind, subject, "cvs" if kind == "creator" else a.signal, a.direction, a.horizon,
                        a.outcome, a.p, a.by, a.note, label)
        print(f"call {row['id']} logged at {row['made_at']}: {label} {a.direction} ({row['signal']} = {row['score']})")
    elif a.command == "resolve":
        if not (a.id and a.outcome):
            raise SystemExit("resolve needs --id and --outcome hit|miss|void (and --note with the evidence)")
        calls.resolve(a.id, a.outcome, a.note, a.by)
        print(f"call {a.id} resolved: {a.outcome}")
    elif a.command == "calls":
        problems = calls.verify(calls.CALLS, calls.CALL_FIELDS) + calls.verify(calls.RESOLUTIONS, calls.RES_FIELDS)
        for c in calls.table():
            print(f"{c['id']:>3}  {c['made_at'][:10]}  {c['label'] or c['subject']:<28} {c['direction']:<4} "
                  f"{c['signal']}={c['score']}  {c['horizon_weeks']}w  {c['outcome']}")
        print("log intact" if not problems else "LOG PROBLEMS:\n" + "\n".join(problems))
    elif a.command == "migrate-ctncs":
        print(f"rewrote v1.0 codes to v2.0: {ctncs.migrate(con)}")
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
