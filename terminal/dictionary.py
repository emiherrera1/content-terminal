"""Write docs/DATA_DICTIONARY.md from the live schema: python -m terminal dictionary (run against data/demo.duckdb
by default, so every table exists)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _doc_lines(mod) -> dict:
    out = {}
    for line in (mod.__doc__ or "").splitlines():
        m = re.match(r"\s{2}(\w+)\s{2,}(.*)", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def write(con, path: Path = ROOT / "docs" / "DATA_DICTIONARY.md") -> Path:
    from . import analytics, formulas, later, metrics
    desc = {}
    for mod in (formulas, later, analytics, metrics):
        desc.update(_doc_lines(mod))
    groups = [
        ("Raw data (written by the pulls)", ["channels", "channel_snapshots", "videos", "video_snapshots", "classifications",
                                             "video_classifications", "news"]),
        ("Inputs and hand-kept records", ["outcome_series", "outside_estimates", "classification_audits", "video_comments",
                                          "video_entities", "video_packaging", "creator_review", "topic_crosswalk"]),
        ("Core formula tables (critical stage: compute)", formulas.TABLES + ["creator_metrics", "creator_cvs", "cvs_history",
                                                                             "creator_watchlist", "creator_alerts"]),
        ("Later set (stage: later)", later.TABLES),
        ("Statistics (stage: later)", analytics.TABLES),
        ("Orchestration", ["pipeline_runs"]),
    ]
    md = ["# Data dictionary", "", "Generated from the schema by `python -m terminal dictionary`. Every table the terminal reads or "
          "writes, its columns and types. Section numbers point to the Formula Library (Methodology Handbook, Appendix C).", ""]
    by = {}
    for t, c, ty in con.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                                "ORDER BY table_name, ordinal_position").fetchall():
        by.setdefault(t, []).append((c, ty))
    seen = set()
    for title, names in groups:
        md += [f"## {title}", ""]
        for n in dict.fromkeys(names):
            if n not in by or n in seen:
                continue
            seen.add(n)
            md += [f"### `{n}`", ""] + ([desc[n], ""] if n in desc else [])
            md += ["| Column | Type |", "| --- | --- |"] + [f"| `{c}` | {ty} |" for c, ty in by[n]] + [""]
    rest = sorted(t for t in by if t not in seen and not t.startswith("_"))
    md += ["## Views (rebuilt on every compute)", ""] + [f"- `{t}`" + (f": {desc[t]}" if t in desc else "") for t in rest] + [""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(md))
    return path
