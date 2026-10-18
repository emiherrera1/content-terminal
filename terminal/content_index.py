"""Answer "how did woodworking do today": one topic's daily performance from video-level data."""
import datetime as dt

from . import ctncs

LEVEL_COL = {1: "sector", 2: "category", 3: "niche", 4: "subniche"}


def report(con, code: str, day: dt.date | None = None, top: int = 5) -> dict:
    book = ctncs.load()
    level = len(code.split("."))
    col = LEVEL_COL[level]
    day = day or con.execute("SELECT max(snapshot_date) FROM content_daily WHERE code = ?", [code]).fetchone()[0]
    if day is None:
        return {"code": code, "label": " > ".join(ctncs.label_path(book, code)), "day": None}

    def gain(d):
        r = con.execute("SELECT views_gained FROM content_daily WHERE code = ? AND snapshot_date = ?", [code, d]).fetchone()
        return r[0] if r else None

    today = con.execute(
        "SELECT views_gained, videos, channels, new_videos, short_views, median_video_gain "
        "FROM content_daily WHERE code = ? AND snapshot_date = ?", [code, day]).fetchone()
    prior = con.execute(
        "SELECT avg(views_gained), count(*) FROM content_daily WHERE code = ? "
        "AND snapshot_date BETWEEN ? AND ?", [code, day - dt.timedelta(days=7), day - dt.timedelta(days=1)]).fetchone()
    yday = gain(day - dt.timedelta(days=1))
    views = today[0] if today else 0

    def pct(new, old):
        return None if not old else new / old - 1

    top_videos = con.execute(
        f"""SELECT v.title, c.title, t.format_label, t.views_gained, t.is_new
            FROM video_topic_daily t JOIN videos v USING (video_id) LEFT JOIN channels c ON c.channel_id = t.channel_id
            WHERE t.{col} = ? AND t.snapshot_date = ? ORDER BY t.views_gained DESC LIMIT ?""", [code, day, top]).fetchall()
    formats = con.execute(
        "SELECT format, views_gained, videos FROM content_format_daily WHERE code = ? AND snapshot_date = ? "
        "ORDER BY views_gained DESC", [code, day]).fetchall()
    children = con.execute(
        "SELECT code, views_gained, videos FROM content_daily WHERE level = ? AND starts_with(code, ?) "
        "AND snapshot_date = ? ORDER BY views_gained DESC", [level + 1, code + ".", day]).fetchall() if level < 4 else []

    cur = con.execute("SELECT share, share_change_28d, new_creator_share, hhi, cr3, dss, thin, views_per_minute, "
                      "active_creators, breadth FROM topic_current WHERE code = ? AND d = ?", [code, day]).fetchone()
    idx = con.execute("SELECT index_level, trend_level, relative_strength FROM topic_index WHERE code = ? AND d = ?",
                      [code, day]).fetchone()
    alerts = con.execute("SELECT kind, subject, value FROM topic_alerts WHERE code = ? AND d = ?", [code, day]).fetchall()
    keys = ["share", "share_change_28d", "new_creator_share", "hhi", "cr3", "dss", "thin", "views_per_minute",
            "active_creators", "breadth"]
    extra = dict(zip(keys, cur)) if cur else {}
    extra.update(dict(zip(["index_level", "trend_level", "relative_strength"], idx)) if idx else {})
    extra["alerts"] = [{"kind": k, "subject": s, "value": v} for k, s, v in alerts]
    return {
        **extra,
        "code": code, "label": " > ".join(ctncs.label_path(book, code)), "day": day,
        "views_gained": views, "videos": today[1] if today else 0, "channels": today[2] if today else 0,
        "new_videos": today[3] if today else 0,
        "short_share": (today[4] or 0) / views if today and views else None,
        "median_video_gain": today[5] if today else None,
        "vs_yesterday": pct(views, yday), "vs_7day_avg": pct(views, prior[0]), "days_of_history": prior[1],
        "top_videos": [{"title": t, "channel": ch, "format": f, "views_gained": g, "new": bool(n)}
                       for t, ch, f, g, n in top_videos],
        "formats": [{"format": f, "views_gained": g, "videos": n} for f, g, n in formats],
        "children": [{"code": c, "label": book.get(c, {}).get("label", c), "views_gained": g, "videos": n}
                     for c, g, n in children],
    }


def render(r: dict) -> str:
    if r["day"] is None:
        return f"{r['label']} ({r['code']}): no classified videos with daily data yet."

    def pct(x):
        return "n/a" if x is None else f"{x:+.1%}"
    lines = [f"{r['label']}  [{r['code']}]  {r['day']}",
             f"views gained  {r['views_gained']:,}   vs yesterday {pct(r['vs_yesterday'])}   "
             f"vs 7-day avg {pct(r['vs_7day_avg'])}  ({r['days_of_history']} days of history)",
             f"videos {r['videos']:,} from {r['channels']:,} channels, {r['new_videos']:,} new; "
             f"Shorts share {pct(r['short_share']).lstrip('+')}; median video gained {r['median_video_gain'] or 0:,.0f}"]
    if r.get("index_level") is not None:
        lines.append(f"index {r['index_level']:,.1f} (trend {r['trend_level']:,.1f})   "
                     f"relative strength {r['relative_strength'] or 0:,.1f}")
    if r.get("share") is not None or r.get("dss") is not None:
        def num(x, f):
            return "n/a" if x is None else format(x, f)
        lines.append(f"share of parent {num(r.get('share'), '.1%')} ({num(r.get('share_change_28d'), '+.1%')} in 28 days)   "
                     f"new-creator share {num(r.get('new_creator_share'), '.0%')}   HHI {num(r.get('hhi'), ',.0f')}   "
                     f"Demand Signal {num(r.get('dss'), '+.2f')}" + ("   THIN SAMPLE" if r.get("thin") else ""))
    if r.get("alerts"):
        lines.append("alerts: " + ", ".join(f"{a['kind']}{' ' + a['subject'] if a['subject'] else ''}" for a in r["alerts"]))
    if r["children"]:
        lines.append("\nby sub-topic")
        lines += [f"  {c['label']:<32} {c['views_gained']:>12,}  ({c['videos']} videos)" for c in r["children"]]
    if r["formats"]:
        lines.append("\nby format")
        lines += [f"  {f['format']:<32} {f['views_gained']:>12,}  ({f['videos']} videos)" for f in r["formats"]]
    if r["top_videos"]:
        lines.append("\ntop videos")
        lines += [f"  {v['views_gained']:>10,}  {v['title'][:60]}  ({v['channel']}{', new' if v['new'] else ''})"
                  for v in r["top_videos"]]
    return "\n".join(lines)
