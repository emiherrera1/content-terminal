"""Platform monetization rates: each platform's published creator share, read like a central bank's policy rate,
next to the market rate creators actually earn (RPM).

  config/platform_rates.csv          one row per program: the current level, whether it is disclosed, the source
  config/platform_rate_changes.csv   every dated change, effective or scheduled (the decisions log)
  niche_rpm (table)                  YouTube RPM by niche from TubeLab channel estimates, refreshed monthly

The policy rates are public facts kept by hand, each with a source and a verified date; a row is stale when it has
not been re-checked in STALE_DAYS. The RPM side switches on with TUBELAB_API_KEY: one channel search per niche
(5 TubeLab credits each, at most once every RPM_REFRESH_DAYS), long-form channels only, summarized as the median
of each channel's estimated RPM range. TubeLab's figures are estimates; the board labels them that way.
"""
import csv
import datetime as dt
import json
import os
import statistics
import urllib.parse
import urllib.request
from pathlib import Path

from .sources import NotConfigured

ROOT = Path(__file__).resolve().parent.parent
RATES = ROOT / "config" / "platform_rates.csv"
CHANGES = ROOT / "config" / "platform_rate_changes.csv"
STALE_DAYS = 90
RPM_REFRESH_DAYS = 28
TUBELAB = "https://public-api.tubelab.net/v1"
TUBELAB_GAP_S = 6.5      # TubeLab allows 10 requests a minute per key
TUBELAB_RETRIES = 4      # on 429, wait a full minute and try again


def _read(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


def _num(s):
    try:
        return float(s) if s not in ("", None) else None
    except ValueError:
        return None


def changes() -> list[dict]:
    return sorted(({**c, "old": _num(c["old"]), "new": _num(c["new"])} for c in _read(CHANGES)),
                  key=lambda c: c["date"], reverse=True)


def board(today: dt.date | None = None, rpm: list[dict] | None = None) -> list[dict]:
    """One row per program: level, the last effective change and the next scheduled one, freshness, market RPM."""
    today = today or dt.date.today()
    ch = changes()
    out = []
    for r in _read(RATES):
        mine = [c for c in ch if c["id"] == r["id"]]
        past = [c for c in mine if c["date"] <= today.isoformat()]   # a scheduled change counts once its date passes
        ahead = sorted((c for c in mine if c["date"] > today.isoformat()), key=lambda c: c["date"])
        last, nxt = (past[0] if past else None), (ahead[0] if ahead else None)
        verified = dt.date.fromisoformat(r["verified"]) if r["verified"] else None
        delta = None
        if last and last["old"] is not None and last["new"] is not None:
            delta = last["new"] - last["old"]
            if r["unit"] != "%" and last["old"]:
                delta = (last["new"] / last["old"] - 1) * 100   # a dollar rate moves in percent of itself
        level = _num(r["level"])
        if last and last["new"] is not None and r["verified"] and last["date"] > r["verified"]:
            level = last["new"]   # a change announced in advance applies on its date, before anyone re-verifies the row
        out.append({**r, "level": level, "last_date": last["date"] if last else None,
                    "last_change": delta, "last_change_unit": None if delta is None else ("pts" if r["unit"] == "%" else "%"),
                    "last_kind": None if not last else "new" if last["old"] is None and last["new"] is not None
                                 else "terms" if last["new"] is None or not delta else "move",
                    "last_what": last["what"] if last else None, "last_source": last["source"] if last else None,
                    "next_date": nxt["date"] if nxt else None, "next_what": nxt["what"] if nxt else None,
                    "next_new": nxt["new"] if nxt else None, "changes": len(mine),
                    "stale": verified is None or (today - verified).days > STALE_DAYS})
    if rpm:
        mids = [x["rpm_mid"] for x in rpm if x.get("rpm_mid") is not None]
        if mids:
            for r in out:
                if r["id"] == "yt_long":
                    r["market_rpm"] = round(statistics.median(mids), 2)
                    r["market_rpm_low"] = round(min(x["rpm_low"] for x in rpm if x.get("rpm_low") is not None), 2)
                    r["market_rpm_high"] = round(max(x["rpm_high"] for x in rpm if x.get("rpm_high") is not None), 2)
                    r["market_as_of"] = max(str(x["as_of"]) for x in rpm)
    return out


# ---------------------------------------------------------------- market rate: YouTube RPM by niche from TubeLab
DDL = """CREATE TABLE IF NOT EXISTS niche_rpm (as_of DATE, niche_code VARCHAR, query VARCHAR, channels INTEGER,
         tracked_matches INTEGER, rpm_low DOUBLE, rpm_mid DOUBLE, rpm_high DOUBLE, source VARCHAR)"""


def _search(key: str, query: str, size: int = 40, sleep=None) -> list[dict]:
    import time
    import urllib.error
    sleep = sleep or time.sleep
    q = urllib.parse.urlencode([("query", query), ("contentKind", "long-form"), ("monetizationAdsense", "true"),
                                ("size", str(size)), ("language", "en")])
    req = urllib.request.Request(f"{TUBELAB}/search/channels?{q}", headers={"Authorization": f"Api-Key {key}"})
    for attempt in range(TUBELAB_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8")).get("hits", [])
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == TUBELAB_RETRIES:
                raise
            sleep(int(e.headers.get("Retry-After") or 0) or 61)


def summarize(hits: list[dict], tracked: set[str]) -> dict | None:
    """Median of each channel's estimated RPM range; the low and high are the medians of the range ends."""
    lows, highs, mids, matched = [], [], [], 0
    for h in hits:
        m = h.get("monetization") or {}
        lo, hi = _num(m.get("rpmEstimationFrom")), _num(m.get("rpmEstimationTo"))
        if lo is None or hi is None:
            continue
        lows.append(lo); highs.append(hi); mids.append((lo + hi) / 2)
        matched += h.get("id") in tracked
    if not mids:
        return None
    return {"channels": len(mids), "tracked_matches": matched, "rpm_low": statistics.median(lows),
            "rpm_mid": statistics.median(mids), "rpm_high": statistics.median(highs)}


def load_rpm(con, today: dt.date | None = None, label=None, search=None, sleep=None) -> str:
    key = os.environ.get("TUBELAB_API_KEY")
    if not key and search is None:
        raise NotConfigured("niche RPM: set TUBELAB_API_KEY (TubeLab Pro or Team) to pull YouTube RPM estimates by niche")
    today = today or dt.date.today()
    con.execute(DDL)
    last = con.execute("SELECT max(as_of) FROM niche_rpm").fetchone()[0]
    if last and (today - last).days < RPM_REFRESH_DAYS:
        return f"fresh (last pull {last}); next after {last + dt.timedelta(days=RPM_REFRESH_DAYS)}"
    if label is None:
        from .coverage import _label as label
    import time
    if search is None:
        sleep = sleep or time.sleep
        search, gap = (lambda q: _search(key, q, sleep=sleep)), TUBELAB_GAP_S
    else:
        sleep, gap = sleep or (lambda s: None), 0
    niches = [r[0] for r in con.execute("SELECT DISTINCT niche_code FROM channel_niche ORDER BY 1").fetchall()]
    tracked = {r[0] for r in con.execute("SELECT channel_id FROM channels").fetchall()}
    rows, failed = [], []
    for i, code in enumerate(niches):
        if i and gap:
            sleep(gap)
        q = label(code)
        try:
            s = summarize(search(q), tracked)
        except Exception as e:   # one niche failing keeps the rest; it is retried at the next pull
            failed.append(f"{code} ({e})")
            continue
        if s:
            rows.append((today, code, q, s["channels"], s["tracked_matches"], s["rpm_low"], s["rpm_mid"], s["rpm_high"],
                         "TubeLab estimate"))
    if not rows and failed:
        raise RuntimeError(f"every TubeLab search failed, e.g. {failed[0]}")
    con.execute("DELETE FROM niche_rpm WHERE as_of = ?", [today])
    if rows:
        con.executemany("INSERT INTO niche_rpm VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    done = len(niches) - len(failed)
    return (f"{len(rows)} of {len(niches)} niches priced ({done} searches, {5 * done} credits)"
            + (f"; {len(failed)} failed: {', '.join(failed[:3])}" if failed else ""))


def latest_rpm(con, label=lambda c: c) -> list[dict]:
    got = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'niche_rpm'").fetchone()[0]
    if not got:
        return []
    cur = con.execute("SELECT * FROM niche_rpm WHERE as_of = (SELECT max(as_of) FROM niche_rpm) ORDER BY rpm_mid DESC")
    cols = [d[0] for d in cur.description]
    return [{**dict(zip(cols, r)), "niche": label(r[1])} for r in cur.fetchall()]


def demo_rpm(niches: list[tuple[str, str]], as_of: dt.date) -> list[dict]:
    """SYNTHETIC RPM by niche for the demo site."""
    import zlib
    out = []
    for code, name in niches:
        h = zlib.crc32(code.encode()) % 1000
        mid = round(1.5 + h / 1000 * 14, 2)
        out.append({"as_of": as_of, "niche_code": code, "niche": name, "query": name, "channels": 20 + h % 20,
                    "tracked_matches": h % 4, "rpm_low": round(mid * 0.6, 2), "rpm_mid": mid, "rpm_high": round(mid * 1.5, 2),
                    "source": "SYNTHETIC estimate"})
    return sorted(out, key=lambda r: -r["rpm_mid"])
