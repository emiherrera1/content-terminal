"""Holders: who funds creator companies, by kind of capital (venture, growth, private equity, credit, buyout...).

Built from the Rounds feed of the Creator Companies sheet (creator_rounds) and a registry of funders and their type
(config/funders.csv). Each round names its lead and other investors; every name becomes one holding. The round's
instrument is read from how the sheet describes it: a buyout, catalog licensing, revenue share, debt, or equity.
Amounts are per round, never split across investors, so a holder's "in rounds" total is the size of the rounds it
joined, not its own check.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FUNDERS = ROOT / "config" / "funders.csv"
TYPES = ["Venture", "Growth", "Private equity", "Credit", "Creator finance", "Revenue share", "Catalog finance", "Acquirer",
         "Strategic", "Family office", "Accelerator", "Angel", "Founder", "Unclassified"]

_INSTRUMENT = [(r"buyout|acquisition|acquired|brand sale", "Buyout"), (r"catalog", "Catalog license"),
               (r"revenue.share|revenue-based", "Revenue share"), (r"debt|credit|loan|facility|financed", "Debt"),
               (r"instrument not disclosed", "Undisclosed")]


# a lender's or financier's piece of a deal is that instrument whatever the round is called (Breeze lent for a buyout)
_BY_TYPE = {"Credit": "Debt", "Catalog finance": "Catalog license", "Revenue share": "Revenue share"}


def registry() -> dict[str, dict]:
    with open(FUNDERS, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        for name in [r["funder"]] + [a.strip() for a in (r["aliases"] or "").split(";") if a.strip()]:
            out[name.lower()] = r
    return out


def instrument(round_name: str | None) -> str:
    s = (round_name or "").lower()
    return next((v for pat, v in _INSTRUMENT if re.search(pat, s)), "Equity")


def holdings(rounds: list[dict], reg: dict | None = None) -> list[dict]:
    """One row per investor per round."""
    reg = reg if reg is not None else registry()
    out = []
    for r in rounds:
        for role, names in (("Lead", r.get("lead_investors")), ("Participant", r.get("other_investors"))):
            for n in [x.strip() for x in (names or "").split(";") if x.strip()]:
                f = reg.get(n.lower(), {})
                out.append({"funder": f.get("funder") or n, "type": f.get("type") or "Unclassified", "role": role,
                            "company": r.get("company"), "creator": r.get("creator"), "round": r.get("round"),
                            "instrument": _BY_TYPE.get(f.get("type"), instrument(r.get("round"))), "round_date": r.get("round_date"),
                            "amount_m": r.get("amount_m"), "valuation_m": r.get("valuation_m"), "currency": r.get("currency"),
                            "source": r.get("source"), "confidence": r.get("confidence")})
    return out


def holders(rows: list[dict], reg: dict | None = None) -> list[dict]:
    """One row per funder: type, deals, leads, companies backed, size of the rounds joined, latest deal."""
    reg = reg if reg is not None else registry()
    by = {}
    for h in rows:
        b = by.setdefault(h["funder"], {"funder": h["funder"], "type": h["type"], "deals": 0, "led": 0, "companies": set(),
                                         "creators": set(), "instruments": set(), "in_rounds_m": 0.0, "disclosed": 0,
                                         "last_date": None, "last_company": None})
        b["deals"] += 1
        b["led"] += h["role"] == "Lead"
        b["companies"].add(h["company"]); b["creators"].add(h["creator"]); b["instruments"].add(h["instrument"])
        if h["amount_m"] is not None and (h["currency"] or "USD") == "USD":
            b["in_rounds_m"] += float(h["amount_m"]); b["disclosed"] += 1
        d = str(h["round_date"]) if h["round_date"] else None
        if d and (b["last_date"] is None or d > b["last_date"]):
            b["last_date"], b["last_company"] = d, h["company"]
        elif b["last_company"] is None:
            b["last_company"] = h["company"]
    out = []
    for b in by.values():
        f = reg.get(b["funder"].lower(), {})
        out.append({**b, "companies": len(b["companies"]), "company_list": "; ".join(sorted(b["companies"])),
                    "creators": len(b["creators"]), "instruments": ", ".join(sorted(b["instruments"])),
                    "in_rounds_m": round(b["in_rounds_m"], 2) if b["disclosed"] else None,
                    "note": f.get("note") or None, "funder_source": f.get("source") or None})
    return sorted(out, key=lambda x: (-x["deals"], -(x["in_rounds_m"] or 0), x["funder"]))


def demo_types(rows: list[dict]) -> list[dict]:
    """The demo's investors are made up; give each a type so the type filter has something to show."""
    kinds = {"Example Ventures": "Venture", "Sample Growth Partners": "Growth", "Placeholder Capital": "Private equity",
             "Demo Strategic Fund": "Strategic", "Mock Media Holdings": "Acquirer"}
    return [{**r, "type": kinds.get(r["funder"], "Unclassified")} for r in rows]
