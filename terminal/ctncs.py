"""Load the CTNCS codebook (github.com/emiherrera1/ctncs, CC BY 4.0)."""
import csv
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "config" / "ctncs_codebook.csv"


def load(path: str | Path = DEFAULT) -> dict[str, dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return {r["code"]: r for r in csv.DictReader(f)}


def subtree(codebook: dict[str, dict], sectors: list[str], max_level: int = 3) -> list[dict]:
    """Codes under the chosen sectors, down to max_level (3 = niche)."""
    return [r for code, r in codebook.items()
            if code.split(".")[0] in sectors and int(r["level"]) <= max_level]


def label_path(codebook: dict[str, dict], code: str) -> list[str]:
    parts = code.split(".")
    return [codebook[".".join(parts[:i])]["label"] for i in range(1, len(parts) + 1)
            if ".".join(parts[:i]) in codebook]


def topics(codebook: dict[str, dict], max_level: int = 4) -> list[dict]:
    """Topic codes (levels 1 to max_level). Level 5 is format, handled by formats()."""
    return [r for r in codebook.values() if int(r["level"]) <= max_level]


def formats(codebook: dict[str, dict]) -> dict[str, str]:
    """CTNCS level-5 formats: {format id: label}. The id is the last code segment and is the same in every niche."""
    return {r["code"].split(".")[-1]: r["label"] for r in codebook.values() if r["level"] == "5"}


def resolve(codebook: dict[str, dict], query: str, max_level: int = 4) -> list[dict]:
    """Find topic codes by code or by label (exact label first, then partial), shallowest first."""
    q = query.strip().lower()
    if query.strip() in codebook:
        return [codebook[query.strip()]]
    rows = topics(codebook, max_level)
    exact = [r for r in rows if r["label"].lower() == q]
    hits = exact or [r for r in rows if q in r["label"].lower()]
    return sorted(hits, key=lambda r: (int(r["level"]), r["code"]))
