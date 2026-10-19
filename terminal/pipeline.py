"""Jobs as ordered stages, with retries, a run log and quality gates.

    python -m terminal run daily     what the morning schedule runs
    python -m terminal run weekly    outcome series, outside estimates, audit sheet (Monday schedule)
    python -m terminal health        is the data fresh and did the last runs pass? (exit 1 if not)

Every stage writes a row to pipeline_runs: ok, warn, failed or skipped, with attempts and a one-line detail.
A stage marked critical fails the job (the schedule shows red and GitHub emails the repo owner); any other stage
that fails is logged as a warning and the job carries on, so a new or flaky source can never stop the core pull.
A source with no credentials or input files yet is logged as skipped, with what it needs.
"""
import datetime as dt
import os
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from . import sources

ROOT = Path(__file__).resolve().parent.parent
ON_ACTIONS = bool(os.environ.get("GITHUB_ACTIONS"))


@dataclass
class Stage:
    name: str
    run: Callable
    critical: bool = False
    retries: int = 0            # extra attempts for network stages, with backoff
    note: str = ""


@dataclass
class Context:
    con: object
    today: dt.date
    settings: dict
    results: dict = field(default_factory=dict)
    calls_paths: tuple | None = None      # the demo passes its own call log; None = the real one


def _settings() -> dict:
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())


def _annotate(level: str, title: str, msg: str) -> None:
    if ON_ACTIONS:
        print(f"::{level} title={title}::{msg}")


# ---------------------------------------------------------------- stage bodies
def _pull_youtube(ctx):
    from . import youtube
    ids = [c for (c,) in ctx.con.execute("SELECT channel_id FROM channels WHERE coalesce(platform, 'youtube') = 'youtube'").fetchall()]
    ch = youtube.pull_channels(ctx.con, ids, ctx.today)
    vids = youtube.pull_recent_videos(ctx.con, ctx.today, ctx.settings.get("videos_per_channel", 50))
    return f"{ch} channels, {vids} videos"


def _platform(adapter):
    def run(ctx):
        return f"{adapter.pull(ctx.con, ctx.today)} rows"
    return run


def _classify_channels(ctx):
    from . import classify
    return f"{classify.classify_channels(ctx.con, ctx.settings['sectors'])} classified"


def _classify_videos(ctx):
    from . import video_classify
    return f"{video_classify.classify_videos(ctx.con, ctx.settings.get('video_classify_daily_limit'))} classified"


def _news(ctx):
    from . import news
    return f"{news.pull(ctx.con)} articles, {news.tag(ctx.con)} tagged"


def _tag(ctx):
    from . import tagging
    return f"{tagging.tag_packaging(ctx.con)} packaging, {tagging.tag_entities(ctx.con)} entity mentions"


def _compute(ctx):
    from . import metrics
    metrics.build(ctx.con)
    return "core tables rebuilt"


def _later(ctx):
    """The Later set and the statistics. Not critical: a failure here never blocks the core tables or the export."""
    from . import analytics, later
    later.build(ctx.con)
    if ctx.calls_paths:
        analytics.build(ctx.con, None, *ctx.calls_paths)
    else:
        analytics.build(ctx.con)
    return "later and statistics tables rebuilt"


def _comments(ctx):
    return f"{sources.pull_comments(ctx.con, ctx.today)} comments"


def _quality_gate(ctx):
    """Warn (never fail) when today's data looks incomplete, so a partial day is visible in the run log."""
    q = ctx.settings.get("quality", {})
    row = ctx.con.execute("SELECT d, pull_completeness, unclassified_share FROM data_quality_daily ORDER BY d DESC LIMIT 1").fetchone()
    if not row:
        return "no data yet"
    d, comp, uncl = row
    problems = []
    if comp is not None and comp < float(q.get("min_pull_completeness", 0.95)):
        problems.append(f"pull completeness {comp:.0%} on {d}")
    if uncl is not None and uncl > float(q.get("max_unclassified_share", 0.25)):
        problems.append(f"{uncl:.0%} of views from unclassified videos on {d}")
    stale = (ctx.today - d).days if d else None
    if stale and stale > 1:
        problems.append(f"latest data is {stale} days old")
    if problems:
        raise Warning("; ".join(problems))
    return f"{d}: completeness {comp:.0%}" if comp is not None else str(d)


def _export(ctx):
    from . import metrics
    return f"{len(metrics.export(ctx.con))} files"


def _audit_sheet(ctx):
    from . import classify
    return classify.audit_sheet(ctx.con, "video", 25)


def _trends(ctx):
    return f"{sources.pull_google_trends(ctx.con)} weekly values"


def _outcome_csvs(ctx):
    return f"{sources.load_outcome_csvs(ctx.con)} rows"


def _outside(ctx):
    return f"{sources.load_outside_estimates(ctx.con)} rows"


JOBS: dict[str, list[Stage]] = {
    "daily": [
        Stage("pull_youtube", _pull_youtube, critical=True, retries=2),
        *[Stage(f"pull_{a.platform}", _platform(a), retries=1) for a in sources.PLATFORMS],
        Stage("classify_channels", _classify_channels, retries=1),
        Stage("classify_videos", _classify_videos, retries=1),
        Stage("news", _news, retries=1),
        Stage("tag", _tag),
        Stage("comments", _comments, retries=1),
        Stage("compute", _compute, critical=True),
        Stage("later", _later),
        Stage("quality_gate", _quality_gate),
        Stage("export", _export, critical=True),
    ],
    "weekly": [
        Stage("google_trends", _trends, retries=2),
        Stage("outcome_csvs", _outcome_csvs),
        Stage("outside_estimates", _outside),
        Stage("compute", _compute, critical=True),
        Stage("later", _later),
        Stage("audit_sheet", _audit_sheet),
        Stage("export", _export, critical=True),
    ],
    # everything that needs no API: rebuild every table from what is stored (after a formula or config change)
    "rebuild": [
        Stage("tag", _tag),
        Stage("compute", _compute, critical=True),
        Stage("later", _later),
        Stage("quality_gate", _quality_gate),
        Stage("export", _export, critical=True),
    ],
}


def run(con, job: str, today: dt.date | None = None, stages: list[Stage] | None = None, sleep=time.sleep,
        calls_paths: tuple | None = None) -> dict:
    stages = stages if stages is not None else JOBS[job]
    ctx = Context(con=con, today=today or dt.date.today(), settings=_settings(), calls_paths=calls_paths)
    run_id = uuid.uuid4().hex[:12]
    failed_critical = []
    for st in stages:
        started = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        attempts, status, detail = 0, "ok", ""
        while True:
            attempts += 1
            try:
                detail = str(st.run(ctx) or "")
                status = "ok"
                break
            except sources.NotConfigured as e:
                status, detail = "skipped", str(e)
                break
            except Warning as e:
                status, detail = "warn", str(e)
                break
            except Exception as e:  # noqa: BLE001 - every failure is logged, critical ones end the job
                detail = f"{type(e).__name__}: {e}"
                if attempts <= st.retries:
                    sleep(min(60, 5 * 2 ** (attempts - 1)))
                    continue
                status = "failed"
                if os.environ.get("TERMINAL_DEBUG"):
                    traceback.print_exc()
                break
        con.execute("INSERT INTO pipeline_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [run_id, job, st.name, started, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None), status, attempts, detail[:500]])
        ctx.results[st.name] = (status, detail)
        print(f"{st.name:<20} {status:<8} {detail}")
        if status == "failed" and st.critical:
            _annotate("error", f"{job}: {st.name} failed", detail)
            failed_critical.append(st.name)
            break
        if status in ("failed", "warn"):
            _annotate("warning", f"{job}: {st.name} {status}", detail)
    return {"run_id": run_id, "ok": not failed_critical, "failed": failed_critical, "results": ctx.results}


def health(con, today: dt.date | None = None, max_age_days: int = 1) -> tuple[bool, list[str]]:
    today = today or dt.date.today()
    lines, ok = [], True
    last = con.execute("SELECT max(snapshot_date) FROM channel_snapshots").fetchone()[0]
    if last is None or (today - last).days > max_age_days:
        ok = False
        lines.append(f"STALE: latest channel snapshot {last}")
    else:
        lines.append(f"fresh: latest channel snapshot {last}")
    q = con.execute("SELECT pull_completeness FROM data_quality_daily ORDER BY d DESC LIMIT 1").fetchone() \
        if con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'data_quality_daily'").fetchone()[0] else None
    if q and q[0] is not None:
        lines.append(f"pull completeness {q[0]:.0%}")
    for job in ("daily", "weekly"):
        r = con.execute("""SELECT run_id, max(started_at), count(*) FILTER (WHERE status = 'failed'),
                                  count(*) FILTER (WHERE status = 'warn'), count(*) FILTER (WHERE status = 'skipped')
                           FROM pipeline_runs WHERE job = ? GROUP BY run_id ORDER BY 2 DESC LIMIT 1""", [job]).fetchone()
        if r:
            lines.append(f"last {job} run {r[1]:%Y-%m-%d %H:%M} UTC: {r[2]} failed, {r[3]} warnings, {r[4]} skipped")
            ok = ok and r[2] == 0
        else:
            lines.append(f"no {job} run logged yet")
    return ok, lines
