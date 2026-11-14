"""Operations board: what is working, what broke, what updated, and what it costs, in one file for the OPS screen.

Built from pipeline_runs (every stage of every run), api_usage (terminal/usage.py), the data itself (how fresh each
source is) and config/costs.yaml. It is owner-only: the live site serves it under /api/, which guests cannot open.
"""
import datetime as dt
import re

from . import usage

ET = dt.timezone(dt.timedelta(hours=-5))   # display only; stored times are UTC
STATUS = {"ok": "OK", "warn": "WARN", "failed": "FAIL", "skipped": "OFF"}


def _has(con, *t) -> bool:
    got = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    return set(t) <= got


def _one(con, sql, params=None):
    try:
        r = con.execute(sql, params or []).fetchone()
        return r[0] if r else None
    except Exception:  # noqa: BLE001 - a missing table reads as no data
        return None


def _mb(size: str | None) -> float | None:
    m = re.match(r"([\d.]+)\s*(\w+)", size or "")
    if not m:
        return None
    mult = {"bytes": 1 / 2 ** 20, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "TiB": 2 ** 20, "KB": 1 / 1000, "MB": 1, "GB": 1000}
    return float(m.group(1)) * mult.get(m.group(2), 1)


def runs(con, n: int = 40) -> list[dict]:
    if not _has(con, "pipeline_runs"):
        return []
    rows = con.execute("""SELECT run_id, job, stage, started_at, finished_at, status, attempts, detail FROM pipeline_runs
                          WHERE run_id IN (SELECT run_id FROM pipeline_runs GROUP BY run_id ORDER BY max(started_at) DESC LIMIT ?)
                          ORDER BY started_at""", [n]).fetchall()
    by = {}
    for run_id, job, stage, s, f, status, attempts, detail in rows:
        r = by.setdefault(run_id, {"run_id": run_id, "job": job, "started": s, "finished": f, "stages": []})
        r["started"], r["finished"] = min(r["started"], s), max(r["finished"], f or s)
        r["stages"].append({"stage": stage, "status": STATUS.get(status, status), "attempts": attempts,
                            "seconds": round((f - s).total_seconds()) if f and s else None, "detail": detail})
    out = []
    for r in by.values():
        c = {k: sum(1 for x in r["stages"] if x["status"] == k) for k in ("OK", "WARN", "FAIL", "OFF")}
        out.append({**r, **{k.lower(): v for k, v in c.items()}, "minutes": round((r["finished"] - r["started"]).total_seconds() / 60, 1),
                    "status": "FAIL" if c["FAIL"] else "WARN" if c["WARN"] else "OK"})
    return sorted(out, key=lambda r: r["started"], reverse=True)


def board(con, today: dt.date, run_list: list[dict], cfg: dict) -> list[dict]:
    b = []
    def item(area, name, status, last=None, detail=None):
        b.append({"area": area, "item": name, "status": status, "last": last, "detail": detail})

    # data freshness
    snap = _one(con, "SELECT max(snapshot_date) FROM channel_snapshots")
    age = (today - snap).days if snap else None
    item("Data", "YouTube daily pull", "FAIL" if age is None or age > 2 else "WARN" if age > 1 else "OK", snap,
         f"{_one(con, 'SELECT count(DISTINCT channel_id) FROM channel_snapshots WHERE snapshot_date = ?', [snap]) or 0} channels in the latest pull" if snap else "no pull yet")
    comp = _one(con, "SELECT pull_completeness FROM data_quality_daily ORDER BY d DESC LIMIT 1")
    if comp is not None:
        item("Data", "Pull completeness", "OK" if comp >= 0.95 else "WARN", None, f"{comp:.0%} of tracked channels updated (target 95%)")
    backlog = _one(con, """SELECT count(*) FROM videos v LEFT JOIN video_classifications c USING (video_id)
                           WHERE c.video_id IS NULL AND v.published_at >= now() - INTERVAL 60 DAY""") if _has(con, "video_classifications") else None
    open_b = _one(con, "SELECT count(*) FROM classify_batches WHERE collected_at IS NULL") if _has(con, "classify_batches") else 0
    if backlog is not None:
        item("Data", "Video labels (topic and format)", "OK" if backlog < 500 else "WARN", None,
             f"{backlog:,} recent videos waiting" + (f"; {open_b} batch(es) processing" if open_b else ""))
    news = _one(con, "SELECT max(published_at) FROM news") if _has(con, "news") else None
    if news:
        item("Data", "News", "OK" if (today - news.date()).days <= 3 else "WARN", news.date(), "latest article")
    rpm = _one(con, "SELECT max(as_of) FROM niche_rpm") if _has(con, "niche_rpm") else None
    priced = _one(con, "SELECT count(DISTINCT niche_code) FROM niche_rpm") if rpm else 0
    niches = _one(con, "SELECT count(DISTINCT niche_code) FROM channel_niche") if _has(con, "channel_niche") else 0
    item("Data", "TubeLab RPM by niche", "OFF" if not rpm else "OK" if priced >= (niches or 0) else "WARN", rpm,
         f"{priced} of {niches} niches priced; refreshed every 28 days" if rpm else "not pulled yet (needs TUBELAB_API_KEY)")
    from . import rates
    stale = [r["program"] for r in rates.board(today) if r["stale"]]
    item("Data", "Platform payout rates", "WARN" if stale else "OK", None,
         f"{len(stale)} program(s) not re-checked in 90 days" if stale else "all re-checked within 90 days")
    # metrics that need history before they fill in
    first = _one(con, "SELECT min(snapshot_date) FROM channel_daily WHERE views_gained IS NOT NULL") if _has(con, "channel_daily") else None
    n_scores = _one(con, "SELECT count(*) FROM niche_opportunity") if _has(con, "niche_opportunity") else 0
    item("Metrics", "Niche scores and indexes (CCOS)", "OK" if n_scores else "WAIT", None,
         f"{n_scores} niches scored" if n_scores else f"start {first + dt.timedelta(days=13):%b %d} (need 14 days of history)" if first else "need history")
    n_risk = _one(con, "SELECT count(*) FROM creator_risk") if _has(con, "creator_risk") else 0
    item("Metrics", "Risk stats", "OK" if n_risk else "WAIT", None,
         f"{n_risk} creators" if n_risk else f"start {first + dt.timedelta(days=37):%b %d} (need 30 days of returns)" if first else "need history")
    n_rev = _one(con, "SELECT count(*) FROM creator_revenue") if _has(con, "creator_revenue") else 0
    item("Metrics", "Revenue and catalog value", "OK" if n_rev else "WAIT", None, f"{n_rev} creators priced" if n_rev else "next daily run")
    # the latest run of each job, stage by stage: anything not OK is listed
    for job in ("daily", "weekly"):
        last = next((r for r in run_list if r["job"] == job), None)
        if not last:
            item("Runs", f"{job.title()} job", "OFF", None, "no run logged yet")
            continue
        item("Runs", f"{job.title()} job", last["status"], last["started"].date(),
             f"{last['ok']} ok, {last['warn']} warnings, {last['fail']} failed, {last['off']} off · {last['minutes']} min")
        for s in last["stages"]:
            if s["status"] != "OK":
                item("Runs", f"{job} · {s['stage']}", s["status"], last["started"].date(), s["detail"])
    # storage and backups
    size = _one(con, "SELECT database_size FROM pragma_database_size() WHERE database_name = current_database()")
    mb = _mb(size)
    cap = ((cfg.get("motherduck") or {}).get("free_storage_gb") or 0) * 1024
    if mb is not None:
        item("Storage", "Database size", "WARN" if cap and mb > 0.8 * cap else "OK", None,
             f"{mb:,.0f} MB" + (f" of {cap / 1024:.0f} GB on the plan ({mb / cap:.1%})" if cap else ""))
    item("Storage", "Backups", "OFF", None, "Not set up yet: weekly export of the database to cheap storage (planned)")
    return b


def costs(con, today: dt.date, cfg: dict, run_list: list[dict]) -> dict:
    month0 = today.replace(day=1)
    days_in = (month0.replace(month=month0.month % 12 + 1, year=month0.year + (month0.month == 12)) - month0).days
    daily, by_service, used = [], {}, {}
    if _has(con, "api_usage"):
        for d, service, metric, amount, cost in con.execute(
                """SELECT ts::DATE, service, metric, sum(amount), sum(cost_usd) FROM api_usage
                   WHERE ts::DATE > ? GROUP BY ALL ORDER BY 1""", [today - dt.timedelta(days=60)]).fetchall():
            daily.append({"day": d, "service": service, "metric": metric, "amount": amount, "cost": round(cost, 4)})
            if d >= month0:
                by_service[service] = by_service.get(service, 0) + cost
                used[(service, metric)] = used.get((service, metric), 0) + amount
    fixed = {k: v for k, v in (cfg.get("subscriptions") or {}).items()}
    var = sum(by_service.values())
    elapsed = (today - month0).days + 1
    fixed_total = sum(v for v in fixed.values() if v)
    yt_today = sum(x["amount"] for x in daily if x["service"] == "YouTube Data API" and x["day"] == today)
    tl = (cfg.get("tubelab") or {}).get("monthly_credits")
    gh_min = sum(r["minutes"] for r in run_list if r["started"].date() >= month0)
    limits = [{"name": "YouTube quota today", "used": yt_today, "limit": (cfg.get("youtube") or {}).get("daily_quota_units", 10000), "unit": "units"},
              {"name": "TubeLab credits this month", "used": used.get(("TubeLab", "credits"), 0), "limit": tl, "unit": "credits"},
              {"name": "GitHub Actions this month", "used": round(gh_min), "limit": None, "unit": "minutes",
               "note": "free while the repository is public"}]
    return {"month": f"{month0:%B %Y}", "variable": {k: round(v, 2) for k, v in by_service.items()}, "variable_total": round(var, 2),
            "fixed": fixed, "fixed_total": fixed_total, "unknown_fixed": [k for k, v in fixed.items() if v is None],
            "total_mtd": round(var + fixed_total, 2), "projected_month": round(var / elapsed * days_in + fixed_total, 2),
            "daily": daily, "limits": limits,
            "note": "Variable costs are counted from what each run used, at the prices in config/costs.yaml. Fixed costs are monthly plans."}


def build(con, today: dt.date | None = None) -> dict:
    today = today or dt.datetime.now(ET).date()
    cfg = usage.prices()
    rl = runs(con)
    return {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "today": today,
            "board": board(con, today, rl, cfg), "runs": rl, "costs": costs(con, today, cfg, rl)}
