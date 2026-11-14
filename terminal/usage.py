"""Usage and cost, counted as the pipeline runs.

API calls add to a buffer (Claude tokens, YouTube quota units, TubeLab credits); the pipeline writes the buffer to
api_usage after every stage, tagged with the run and stage. Prices come from config/costs.yaml, so a dollar figure here is
what the provider's published price says the usage cost, not an invoice.
"""
import datetime as dt
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COSTS = ROOT / "config" / "costs.yaml"
DDL = """CREATE TABLE IF NOT EXISTS api_usage (ts TIMESTAMP, run_id VARCHAR, job VARCHAR, stage VARCHAR, service VARCHAR,
         metric VARCHAR, amount DOUBLE, cost_usd DOUBLE, detail VARCHAR)"""
YT_UNITS = {"search": 100}   # every other YouTube Data API read used here costs 1 unit
BUF: list[tuple] = []


def prices() -> dict:
    try:
        return yaml.safe_load(COSTS.read_text()) or {}
    except FileNotFoundError:
        return {}


def add(service: str, metric: str, amount: float, cost: float = 0.0, detail: str | None = None) -> None:
    BUF.append((service, metric, float(amount), float(cost), detail))


def youtube(path: str) -> None:
    add("YouTube Data API", "units", YT_UNITS.get(path, 1), 0.0, path)


def tubelab(searches: int = 1) -> None:
    per = (prices().get("tubelab") or {}).get("credits_per_search", 5)
    add("TubeLab", "credits", per * searches, 0.0, "search/channels")


def anthropic(msg, model: str, purpose: str, batch: bool = False) -> None:
    """Tokens from a Claude reply's usage block, priced from costs.yaml."""
    u = getattr(msg, "usage", None)
    if u is None:
        return
    p = (prices().get("anthropic") or {})
    rate = p.get(model) or {}
    disc = p.get("batch_discount", 0.5) if batch else 1.0
    toks = {"input": getattr(u, "input_tokens", 0) or 0, "output": getattr(u, "output_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0, "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0}
    cost = sum(toks[k] * float(rate.get(k, 0) or 0) for k in toks) / 1e6 * disc
    add("Claude API", "tokens", sum(toks.values()), cost, f"{purpose} · {model}{' · batch' if batch else ''}")


def flush(con, run_id: str | None = None, job: str | None = None, stage: str | None = None) -> int:
    if not BUF:
        return 0
    con.execute(DDL)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rows = [(now, run_id, job, stage, *b) for b in BUF]
    con.executemany("INSERT INTO api_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    BUF.clear()
    return len(rows)
