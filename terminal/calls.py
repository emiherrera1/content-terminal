"""The call log (Formula Library 8.1): every signal the fund acts on or writes about, dated before the outcome.

Calls are appended to calls/calls.csv and outcomes to calls/resolutions.csv. Neither file is ever edited:
each row carries the SHA-256 of the row before it, so changing or deleting an earlier row breaks the chain
and `verify` reports it. Committing the files to git adds an outside timestamp for every call.
"""
import csv
import datetime as dt
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALLS = ROOT / "calls" / "calls.csv"
RESOLUTIONS = ROOT / "calls" / "resolutions.csv"

CALL_FIELDS = ["id", "made_at", "subject_type", "subject", "label", "signal", "score", "direction",
               "horizon_weeks", "outcome_series", "probability", "made_by", "note", "prev_hash", "hash"]
RES_FIELDS = ["call_id", "resolved_at", "outcome", "evidence", "resolved_by", "prev_hash", "hash"]
GENESIS = "0" * 64


def _hash(row: dict, fields: list[str]) -> str:
    body = "|".join(str(row.get(f, "")) for f in fields if f != "hash")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _append(path: Path, fields: list[str], row: dict) -> dict:
    rows = _rows(path)
    row["prev_hash"] = rows[-1]["hash"] if rows else GENESIS
    row["hash"] = _hash(row, fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})
    return row


def current_score(con, subject_type: str, subject: str, signal: str):
    """The value of the signal at the moment of the call, read from the terminal's own tables."""
    if subject_type == "topic":
        col = {"dss": "dss", "index": "index_level", "wow": "wow", "share": "share",
               "new_creator_share": "new_creator_share"}.get(signal, "dss")
        table = "topic_index" if col == "index_level" else "topic_series"
        r = con.execute(f"SELECT {col} FROM {table} WHERE code = ? ORDER BY d DESC LIMIT 1", [subject]).fetchone()
    else:
        r = con.execute("SELECT cvs FROM creator_cvs WHERE channel_id = ?", [subject]).fetchone()
    return None if not r or r[0] is None else round(float(r[0]), 4)


def add(con, subject_type: str, subject: str, signal: str, direction: str, horizon_weeks: int,
        outcome_series: str, probability: float | None = None, made_by: str = "", note: str = "",
        label: str = "", path: Path = CALLS) -> dict:
    if subject_type not in ("topic", "creator"):
        raise ValueError("subject_type must be topic or creator")
    if direction not in ("up", "down"):
        raise ValueError("direction must be up or down")
    if probability is not None and not 0 <= probability <= 1:
        raise ValueError("probability must be between 0 and 1")
    rows = _rows(path)
    row = {"id": len(rows) + 1, "made_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "subject_type": subject_type, "subject": subject, "label": label, "signal": signal,
           "score": current_score(con, subject_type, subject, signal) if con is not None else None,
           "direction": direction, "horizon_weeks": int(horizon_weeks), "outcome_series": outcome_series,
           "probability": "" if probability is None else probability, "made_by": made_by, "note": note}
    return _append(path, CALL_FIELDS, row)


def resolve(call_id: int, outcome: str, evidence: str, resolved_by: str = "",
            calls_path: Path = CALLS, path: Path = RESOLUTIONS) -> dict:
    if outcome not in ("hit", "miss", "void"):
        raise ValueError("outcome must be hit, miss or void")
    if not any(r["id"] == str(call_id) for r in _rows(calls_path)):
        raise ValueError(f"no call {call_id}")
    if any(r["call_id"] == str(call_id) for r in _rows(path)):
        raise ValueError(f"call {call_id} is already resolved")
    row = {"call_id": call_id, "resolved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "outcome": outcome, "evidence": evidence, "resolved_by": resolved_by}
    return _append(path, RES_FIELDS, row)


def verify(path: Path, fields: list[str]) -> list[str]:
    """Problems found in the hash chain; an empty list means the file is intact."""
    problems, prev = [], GENESIS
    for i, r in enumerate(_rows(path), start=1):
        if r["prev_hash"] != prev:
            problems.append(f"row {i}: does not follow the row before it (a row was removed or reordered)")
        if _hash(r, fields) != r["hash"]:
            problems.append(f"row {i}: contents changed after it was written")
        prev = r["hash"]
    return problems


def table(calls_path: Path = CALLS, res_path: Path = RESOLUTIONS) -> list[dict]:
    res = {r["call_id"]: r for r in _rows(res_path)}
    return [{**c, "outcome": res.get(c["id"], {}).get("outcome", "open")} for c in _rows(calls_path)]
