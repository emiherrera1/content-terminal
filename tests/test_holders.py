"""Holders: funders of creator companies by type of capital, from the Rounds feed and the funder registry."""
from terminal import holders

ROUNDS = [
    {"company": "Smosh", "creator": "Smosh", "round": "Founder buyout (debt-financed)", "lead_investors": "Breeze Financial",
     "other_investors": "Mythical Entertainment", "amount_m": None, "currency": "USD", "round_date": "2023-06-20", "source": "https://x"},
    {"company": "Dude Perfect", "creator": "Dude Perfect", "round": "Growth equity (private equity)", "lead_investors": "Highmount Capital",
     "other_investors": None, "amount_m": 100.0, "currency": "USD", "round_date": "2024-04-09", "source": "https://x"},
    {"company": "KM Tools", "creator": "KM", "round": "Creator Fund (seed)", "lead_investors": "Slow Ventures", "other_investors": "",
     "amount_m": 2.0, "currency": "USD", "round_date": "2025-08-11", "source": "https://x"},
    {"company": "Steven.com", "creator": "DOAC", "round": "Equity", "lead_investors": "Slow Ventures; New Fund LP",
     "other_investors": None, "amount_m": 10.0, "currency": "EUR", "round_date": "2025-10-27", "source": "https://x"},
]


def test_holdings_types_and_instruments():
    h = {(x["funder"], x["company"]): x for x in holders.holdings(ROUNDS)}
    assert h[("Breeze Financial", "Smosh")]["type"] == "Credit" and h[("Breeze Financial", "Smosh")]["instrument"] == "Debt"
    assert h[("Mythical Entertainment", "Smosh")]["instrument"] == "Buyout" and h[("Mythical Entertainment", "Smosh")]["role"] == "Participant"
    assert h[("Highmount Capital", "Dude Perfect")]["type"] == "Private equity"
    assert h[("New Fund LP", "Steven.com")]["type"] == "Unclassified"


def test_holders_rollup():
    by = {x["funder"]: x for x in holders.holders(holders.holdings(ROUNDS))}
    slow = by["Slow Ventures"]
    assert (slow["deals"], slow["led"], slow["companies"]) == (2, 2, 2)
    assert slow["in_rounds_m"] == 2.0          # the EUR round is left out of the USD total
    assert slow["last_date"] == "2025-10-27" and slow["last_company"] == "Steven.com"
    assert by["Breeze Financial"]["in_rounds_m"] is None


def test_registry_covers_every_live_type():
    reg = holders.registry()
    assert reg["uta"]["funder"] == "United Talent Agency"     # aliases resolve
    assert {r["type"] for r in reg.values()} <= set(holders.TYPES)
    assert all(r["source"].startswith("http") for r in reg.values())
