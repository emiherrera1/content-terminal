"""Load the CTNCS v2.0 codebook (github.com/emiherrera1/ctncs, CC BY 4.0).

v2.0 has two independent parts:
- topics: sector (01) > category (01.01) > niche (01.01.001) > sub-niche (01.01.001.001)
- formats: how a piece is made (Tutorials, Reviews, DIY...), a two-digit code that is the same everywhere

The files in config/ctncs/ are exported from the CTNCS v2.0 codebook sheet. Codes are never reused once retired.
config/ctncs/crosswalk_v1_v2.csv maps v1.0 codes (02.05.10.19) to v2.0 (02.05.010.019).
"""
import csv
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "config" / "ctncs"
DEFAULT = DIR / "topics.csv"
FORMATS = DIR / "formats.csv"
CROSSWALK = DIR / "crosswalk_v1_v2.csv"


def load(path: str | Path = DEFAULT) -> dict[str, dict]:
    """Topic codes, keyed by code. Each row has code, parent_code, level, label, definition, common_formats."""
    with open(path, newline="", encoding="utf-8") as f:
        return {r["code"]: r for r in csv.DictReader(f) if r.get("status") != "Removed"}


def subtree(codebook: dict[str, dict], sectors: list[str], max_level: int = 3) -> list[dict]:
    """Codes under the chosen sectors, down to max_level (3 = niche)."""
    return [r for code, r in codebook.items()
            if code.split(".")[0] in sectors and int(r["level"]) <= max_level]


def label_path(codebook: dict[str, dict], code: str) -> list[str]:
    parts = code.split(".")
    return [codebook[".".join(parts[:i])]["label"] for i in range(1, len(parts) + 1)
            if ".".join(parts[:i]) in codebook]


def topics(codebook: dict[str, dict], max_level: int = 4) -> list[dict]:
    return [r for r in codebook.values() if int(r["level"]) <= max_level]


def format_rows(path: str | Path = FORMATS) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["retired"].upper() != "TRUE"]


def formats(codebook: dict | None = None, path: str | Path = FORMATS) -> dict[str, str]:
    """Active formats: {two-digit code: label}. (codebook is accepted for older callers and ignored.)"""
    return {r["code"]: r["label"] for r in format_rows(path)}


def resolve(codebook: dict[str, dict], query: str, max_level: int = 4) -> list[dict]:
    """Find topic codes by code (v2.0, or v1.0 via the crosswalk) or by label: exact label first, then partial."""
    q = query.strip()
    if q in codebook:
        return [codebook[q]]
    v2 = crosswalk().get(q)
    if v2 and v2 in codebook:
        return [codebook[v2]]
    rows = topics(codebook, max_level)
    exact = [r for r in rows if r["label"].lower() == q.lower()]
    hits = exact or [r for r in rows if q.lower() in r["label"].lower()]
    return sorted(hits, key=lambda r: (int(r["level"]), r["code"]))


def crosswalk(path: str | Path = CROSSWALK) -> dict[str, str]:
    """{v1.0 code: v2.0 code}"""
    with open(path, newline="", encoding="utf-8") as f:
        return {r["v1_code"]: r["v2_code"] for r in csv.DictReader(f)}


def to_v2(code: str | None, xwalk: dict[str, str] | None = None) -> str | None:
    """Map a v1.0 code to v2.0; v2.0 codes and 'unclassified' pass through."""
    if not code or code == "unclassified":
        return code
    xwalk = xwalk or crosswalk()
    if code in xwalk:
        return xwalk[code]
    if code.count(".") >= 2 and len(code.split(".")[2]) == 2:   # v1 shape not in the crosswalk (level 5): pad
        p = code.split(".")
        return ".".join(p[:2] + [s.zfill(3) for s in p[2:4]])
    return code


def migrate(con) -> dict:
    """Rewrite stored v1.0 codes to v2.0 in classifications and video_classifications."""
    xwalk = crosswalk()
    out = {}
    for table, key, code_col, cols in [("classifications", "channel_id", "ctncs_code", ["sector", "category", "niche"]),
                                       ("video_classifications", "video_id", "topic_code",
                                        ["sector", "category", "niche", "subniche"])]:
        n = 0
        for k, code in con.execute(f"SELECT {key}, {code_col} FROM {table}").fetchall():
            new = to_v2(code, xwalk)
            if new == code:
                continue
            parts = new.split(".") if new and new != "unclassified" else []
            levels = [".".join(parts[:i]) if len(parts) >= i else None for i in range(1, len(cols) + 1)]
            con.execute(f"UPDATE {table} SET {code_col} = ?, " + ", ".join(f"{c} = ?" for c in cols) + f" WHERE {key} = ?",
                        [new, *levels, k])
            n += 1
        out[table] = n
    return out
