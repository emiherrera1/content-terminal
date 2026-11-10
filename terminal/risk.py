"""Creator risk statistics, read the way a stock screen reads risk.

The series is attention: views gained per day, smoothed to a 7-day sum A(t). Its daily log change r(t) = ln(A(t)/A(t-1))
plays the part of a daily return. Over the last WINDOW days, with at least MIN_RETURNS returns:

  volatility     standard deviation of r(t), annualized by sqrt(365)
  beta           cov(r_creator, r_niche) / var(r_niche), where the niche series leaves the creator out
  max_drawdown   the largest fall of A(t) from its running peak, as a share of the peak
  growth         ln(A_end / A_start), annualized
  growth_per_risk  growth / volatility (a Sharpe ratio with no risk-free rate)
  hit_share      share of the last 90 days' views from the single biggest video (from creator_metrics)

A creator whose niche has no one else tracked gets no beta.
"""
import math

import numpy as np
import pandas as pd

WINDOW = 90
MIN_RETURNS = 30
SMOOTH = 7


def _series(con) -> pd.DataFrame:
    got = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if not {"channel_daily", "channel_niche", "channel_snapshots"} <= got:
        return pd.DataFrame()
    df = con.execute(f"""
        WITH last AS (SELECT max(snapshot_date) AS d FROM channel_snapshots)
        SELECT d.snapshot_date, d.channel_id, n.niche_code,
               greatest(d.views_gained, 0) / greatest(coalesce(d.days_since_prev, 1), 1) AS gained
        FROM channel_daily d JOIN channel_niche n USING (channel_id), last
        WHERE d.views_gained IS NOT NULL AND d.snapshot_date > last.d - {WINDOW + SMOOTH + 1}""").df()
    return df


def _returns(level: pd.Series) -> pd.Series:
    a = level.rolling(SMOOTH, min_periods=SMOOTH).sum()
    a = a.where(a > 0)
    return np.log(a / a.shift(1)), a


def build(con) -> str:
    con.execute("""CREATE OR REPLACE TABLE creator_risk (channel_id VARCHAR, niche_code VARCHAR, days INTEGER, volatility DOUBLE,
                   beta DOUBLE, max_drawdown DOUBLE, growth DOUBLE, growth_per_risk DOUBLE, hit_share DOUBLE)""")
    df = _series(con)
    if df.empty:
        return "no history yet"
    wide = df.pivot_table(index="snapshot_date", columns="channel_id", values="gained", aggfunc="sum").sort_index()
    wide = wide.asfreq("D") if isinstance(wide.index, pd.DatetimeIndex) else wide
    niche_of = df.drop_duplicates("channel_id").set_index("channel_id")["niche_code"]
    niche_tot = {k: wide[[c for c in cols if c in wide.columns]].sum(axis=1, min_count=1)
                 for k, cols in niche_of.groupby(niche_of).groups.items()}
    hits = {}
    if con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'creator_metrics'").fetchone()[0]:
        try:
            hits = dict(con.execute("SELECT channel_id, top_video_share FROM creator_metrics").fetchall())
        except Exception:
            hits = {}
    out = []
    for ch in wide.columns:
        r, a = _returns(wide[ch])
        r = r.replace([np.inf, -np.inf], np.nan).dropna()
        if len(r) < MIN_RETURNS:
            continue
        vol = float(r.std(ddof=1) * math.sqrt(365))
        nk = niche_of.get(ch)
        beta = None
        others = niche_tot[nk] - wide[ch].fillna(0) if nk in niche_tot else None
        if others is not None and (niche_of == nk).sum() > 1:
            rn, _ = _returns(others)
            j = pd.concat([r, rn.replace([np.inf, -np.inf], np.nan)], axis=1, join="inner").dropna()
            if len(j) >= MIN_RETURNS and j.iloc[:, 1].var(ddof=1) > 0:
                beta = float(j.cov().iloc[0, 1] / j.iloc[:, 1].var(ddof=1))
        lvl = a.dropna()
        dd = float((1 - lvl / lvl.cummax()).max()) if len(lvl) else None
        growth = float(math.log(lvl.iloc[-1] / lvl.iloc[0]) * 365 / max(len(lvl) - 1, 1)) if len(lvl) > 1 else None
        gpr = growth / vol if growth is not None and vol > 0 else None
        out.append((ch, nk, len(r), vol, beta, dd, growth, gpr, hits.get(ch)))
    if out:
        con.executemany("INSERT INTO creator_risk VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", out)
    return f"{len(out)} creators with {MIN_RETURNS}+ days of returns"
