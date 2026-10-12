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
