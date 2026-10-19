"""Formula Library items that need statistics beyond SQL. Rebuilt by `compute` (weekly is enough).

  topic_ci          3.21 bootstrap 5th-95th percentile range for each topic's week-over-week growth
  lead_lag          6.6  correlation of weekly intent-weighted attention growth with an outcome series' growth,
                         at leads of 0-26 weeks; is_best marks the lead with the highest correlation
  predictive_tests  6.7  Granger test: does lagged attention improve a forecast of the outcome beyond the
                         outcome's own history, in sample (F-test) and out of sample (last 25% of weeks)
  call_scores       8.2-8.5 each call checked against its outcome series: hit or miss, lead time, Brier score,
                         and the base rate of the same rule on randomly chosen topics on the same date
  call_summary      8.2-8.5 the track record in one row
  composite_index   9.3  per-platform chained attention index and a follower-weighted composite (weights reset quarterly)
  platform_lead     9.4  the 6.6 lead-lag run between platforms: which platform moves first in each topic

Every table is written even when its inputs are missing, so the terminal always finds it (empty until data arrives).
"""
import datetime as dt
import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import calls as calls_mod, formulas


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _write(con, name: str, df: pd.DataFrame, columns: dict) -> None:
    """Replace a table with df; an empty df still creates the table with typed columns."""
    cols = ", ".join(f'"{c}" {t}' for c, t in columns.items())
    con.execute(f"CREATE OR REPLACE TABLE {name} ({cols})")
    if len(df):
        con.register("_df", df[list(columns)])
        con.execute(f"INSERT INTO {name} SELECT * FROM _df")
        con.unregister("_df")


# ---------------------------------------------------------------- 3.21
def topic_ci(con, cfg) -> pd.DataFrame:
    L = cfg["later"]
    B, lo, hi = int(L["bootstrap_samples"]), float(L["ci_low"]), float(L["ci_high"])
    df = con.execute(f"""
        WITH last AS (SELECT max(d) AS ld FROM creator_topic_daily)
        SELECT t.level, t.code, t.channel_id,
               coalesce(sum(t.a) FILTER (WHERE t.d > last.ld - 7), 0) AS a7,
               coalesce(sum(t.a) FILTER (WHERE t.d <= last.ld - 7), 0) AS a7p, last.ld AS d
        FROM creator_topic_daily t, last
        WHERE t.d > last.ld - 14 AND t.level BETWEEN 1 AND {int(L['ci_max_level'])}
        GROUP BY ALL""").df()
    out = []
    for (level, code), g in df.groupby(["level", "code"]):
        a7, a7p = g["a7"].to_numpy(float), g["a7p"].to_numpy(float)
        if len(g) < 2 or a7p.sum() <= 0:
            continue
        rng = np.random.default_rng(_seed(code, g["d"].iloc[0]))
        idx = rng.integers(0, len(g), size=(B, len(g)))
        den = a7p[idx].sum(axis=1)
        wow = np.where(den > 0, a7[idx].sum(axis=1) / np.where(den > 0, den, 1) - 1, np.nan)
        wow = wow[~np.isnan(wow)]
        out.append({"d": g["d"].iloc[0], "level": int(level), "code": code, "wow": a7.sum() / a7p.sum() - 1,
                    "wow_low": float(np.quantile(wow, lo)), "wow_high": float(np.quantile(wow, hi)),
                    "creators": len(g), "samples": len(wow)})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- weekly series
def weekly_attention(con, column: str = "ia") -> pd.DataFrame:
    """Weekly intent-weighted attention per topic (complete weeks only)."""
    return con.execute(f"""
        SELECT CAST(date_trunc('week', d) AS DATE) AS week, code, sum({column}) AS x, count(*) AS days
        FROM topic_series GROUP BY ALL HAVING count(*) = 7 AND sum({column}) > 0""").df()


def _dlog(s: pd.Series) -> pd.Series:
    s = s.sort_index()
    s = s[s > 0]
    full = pd.date_range(s.index.min(), s.index.max(), freq="7D") if len(s) else s.index
    return np.log(s.reindex(full)).diff()


def lead_lag_curve(x: pd.Series, y: pd.Series, max_lag: int, min_n: int = 16) -> list[tuple[int, float, int]]:
    """rho(L) = corr(dlog x(t - L), dlog y(t)) for L = 0..max_lag, on weekly series indexed by week start."""
    dx, dy = _dlog(x), _dlog(y)
    out = []
    for lag in range(0, max_lag + 1):
        shifted = dx.copy()
        shifted.index = shifted.index + pd.Timedelta(weeks=lag)
        pair = pd.concat([shifted, dy], axis=1, keys=["x", "y"], sort=True).dropna()
        if len(pair) >= min_n and pair["x"].std() > 0 and pair["y"].std() > 0:
            out.append((lag, float(pair["x"].corr(pair["y"])), len(pair)))
    return out


def outcomes(con) -> pd.DataFrame:
    return con.execute("SELECT series_id, code, label, source, d, value FROM outcome_series ORDER BY series_id, d").df()


# ---------------------------------------------------------------- 6.6
def lead_lag(con, cfg) -> pd.DataFrame:
    wa, oc = weekly_attention(con), outcomes(con)
    rows = []
    for sid, g in oc.groupby("series_id"):
        code = g["code"].iloc[0]
        x = wa[wa["code"] == code].set_index(pd.to_datetime(wa.loc[wa["code"] == code, "week"]))["x"]
        y = g.set_index(pd.to_datetime(g["d"]))["value"]
        curve = lead_lag_curve(x, y, int(cfg["later"]["lead_lag_max_weeks"]))
        best = max(curve, key=lambda r: r[1])[0] if curve else None
        rows += [{"series_id": sid, "code": code, "source": g["source"].iloc[0], "lag_weeks": lag, "rho": rho, "n": n,
                  "is_best": lag == best} for lag, rho, n in curve]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 6.7
def _ols_rss(X, y):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(((y - X @ beta) ** 2).sum()), beta


def granger(x: pd.Series, y: pd.Series, lags: int, test_share: float):
    """F-test that lagged dlog x adds to a forecast of dlog y beyond y's own lags, plus an out-of-sample check."""
    from scipy import stats
    d = pd.concat([_dlog(x), _dlog(y)], axis=1, keys=["x", "y"], sort=True).dropna()
    cols = {}
    for i in range(1, lags + 1):
        cols[f"y{i}"] = d["y"].shift(i)
        cols[f"x{i}"] = d["x"].shift(i)
    d = pd.concat([d, pd.DataFrame(cols)], axis=1).dropna()
    n = len(d)
    if n < 2 * lags + 8:
        return None
    one = np.ones((n, 1))
    Xr = np.hstack([one, d[[f"y{i}" for i in range(1, lags + 1)]].to_numpy()])
    Xu = np.hstack([Xr, d[[f"x{i}" for i in range(1, lags + 1)]].to_numpy()])
    yy = d["y"].to_numpy()
    rss_r, _ = _ols_rss(Xr, yy)
    rss_u, _ = _ols_rss(Xu, yy)
    df2 = n - Xu.shape[1]
    f = ((rss_r - rss_u) / lags) / (rss_u / df2) if rss_u > 0 else math.inf
    p = float(stats.f.sf(f, lags, df2))
    cut = int(round(n * (1 - test_share)))
    _, br = _ols_rss(Xr[:cut], yy[:cut])
    _, bu = _ols_rss(Xu[:cut], yy[:cut])
    rmse_r = float(np.sqrt(((yy[cut:] - Xr[cut:] @ br) ** 2).mean()))
    rmse_u = float(np.sqrt(((yy[cut:] - Xu[cut:] @ bu) ** 2).mean()))
    return {"weeks": n, "lags": lags, "f_stat": float(f), "p_value": p, "rmse_own_history": rmse_r,
            "rmse_with_attention": rmse_u, "out_of_sample_better": rmse_u < rmse_r}


def predictive_tests(con, cfg) -> pd.DataFrame:
    L = cfg["later"]
    wa, oc = weekly_attention(con), outcomes(con)
    rows = []
    for sid, g in oc.groupby("series_id"):
        code = g["code"].iloc[0]
        w = wa[wa["code"] == code]
        x = w.set_index(pd.to_datetime(w["week"]))["x"]
        y = g.set_index(pd.to_datetime(g["d"]))["value"]
        # test at the best lead from 6.6 by shifting attention forward; lags then count from that lead
        curve = lead_lag_curve(x, y, int(L["lead_lag_max_weeks"]))
        lead = max(curve, key=lambda r: r[1])[0] if curve else 0
        xs = x.copy()
        xs.index = xs.index + pd.Timedelta(weeks=max(lead - 1, 0))
        r = granger(xs, y, int(L["granger_lags"]), float(L["granger_test_share"]))
        if r:
            rows.append({"series_id": sid, "code": code, "source": g["source"].iloc[0], "lead_weeks": lead, **r,
                         "passes": r["p_value"] < float(L["granger_p"]) and r["out_of_sample_better"]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 8.2-8.5
def _judge(y: pd.Series, t0: pd.Timestamp, weeks: int, direction: str, typical_weeks: int):
    """Hit if, within the horizon, log y moved in the called direction by more than its typical move."""
    y = y.sort_index()
    y = y[y > 0]
    before = y[y.index <= t0]
    if len(before) < typical_weeks + 4:
        return None
    ly = np.log(before)
    moves = (ly - ly.shift(typical_weeks)).abs().dropna()
    typical = float(moves.median()) if len(moves) else None
    if not typical:
        return None
    base = ly.iloc[-1]
    end = t0 + pd.Timedelta(weeks=weeks)
    after = y[(y.index > t0) & (y.index <= end)]
    sign = 1 if direction == "up" else -1
    for t, v in after.items():
        if sign * (math.log(v) - base) > typical:
            return {"status": "hit", "typical_move": typical, "lead_days": (t - t0).days}
    if y.index.max() >= end:
        return {"status": "miss", "typical_move": typical, "lead_days": None}
    return {"status": "open", "typical_move": typical, "lead_days": None}


def call_scores(con, cfg, calls_path: Path = calls_mod.CALLS, res_path: Path = calls_mod.RESOLUTIONS) -> pd.DataFrame:
    L = cfg["later"]
    oc = outcomes(con)
    series = {sid: g.set_index(pd.to_datetime(g["d"]))["value"] for sid, g in oc.groupby("series_id")}
    rows = []
    for c in calls_mod.table(calls_path, res_path):
        t0 = pd.Timestamp(c["made_at"][:10])
        weeks = int(c["horizon_weeks"])
        p = float(c["probability"]) if c.get("probability") not in ("", None) else None
        sid = c["outcome_series"].strip()
        row = {"call_id": int(c["id"]), "made_on": t0.date(), "subject": c["subject"], "direction": c["direction"],
               "horizon_weeks": weeks, "series_id": sid, "probability": p, "manual_outcome": c["outcome"],
               "status": "no_series", "typical_move": None, "lead_days": None, "brier": None, "base_hit_rate": None}
        if sid in series:
            j = _judge(series[sid], t0, weeks, c["direction"], int(L["typical_move_weeks"]))
            if j:
                row.update(j)
            if row["status"] in ("hit", "miss"):
                if p is not None:
                    row["brier"] = (p - (row["status"] == "hit")) ** 2
                others = [s for s in series if s != sid]
                rng = np.random.default_rng(_seed(c["id"], sid))
                draws = rng.choice(others, size=min(int(L["base_rate_draws"]), len(others) * 10), replace=True) if others else []
                judged = [_judge(series[o], t0, weeks, c["direction"], int(L["typical_move_weeks"])) for o in draws]
                judged = [x["status"] for x in judged if x and x["status"] in ("hit", "miss")]
                row["base_hit_rate"] = sum(s == "hit" for s in judged) / len(judged) if judged else None
        rows.append(row)
    return pd.DataFrame(rows)


def call_summary(scores: pd.DataFrame) -> pd.DataFrame:
    if not len(scores):
        return pd.DataFrame([{"calls": 0, "resolved": 0, "hits": 0, "hit_rate": None, "median_lead_days": None,
                              "brier": None, "base_hit_rate": None, "beat_base_rate": None}])
    r = scores[scores["status"].isin(["hit", "miss"])]
    hit_rate = (r["status"] == "hit").mean() if len(r) else None
    base = r["base_hit_rate"].dropna().mean() if len(r) else None
    return pd.DataFrame([{
        "calls": len(scores), "resolved": len(r), "hits": int((r["status"] == "hit").sum()),
        "hit_rate": hit_rate,
        "median_lead_days": r["lead_days"].dropna().median() if len(r) else None,
        "brier": r["brier"].dropna().mean() if r["brier"].notna().any() else None,
        "base_hit_rate": base,
        "beat_base_rate": hit_rate / base if hit_rate is not None and base else None}])


# ---------------------------------------------------------------- 9.3-9.4
def composite_index(con) -> pd.DataFrame:
    pdl = con.execute("SELECT d, platform, level, code, a, followers FROM platform_daily WHERE a > 0").df()
    if not len(pdl):
        return pd.DataFrame()
    pdl["d"] = pd.to_datetime(pdl["d"])
    out = []
    for (level, code), g in pdl.groupby(["level", "code"]):
        idx = {}
        for plat, gp in g.groupby("platform"):
            s = gp.set_index("d")["a"].sort_index()
            ratio = s / s.shift(1)
            consecutive = s.index.to_series().diff() == pd.Timedelta(days=1)
            ratio = ratio.where(consecutive & (ratio > 0), 1.0).fillna(1.0)
            idx[plat] = 100 * ratio.cumprod()
        frame = pd.DataFrame(idx)
        # weights: each platform's share of the topic's tracked followers on the first day of each quarter
        fol = g.pivot_table(index="d", columns="platform", values="followers", aggfunc="sum").reindex(frame.index)
        q = frame.index.to_period("Q")
        qfirst = fol.groupby(q).transform(lambda c: c.dropna().iloc[0] if c.notna().any() else np.nan)
        w = qfirst.div(qfirst.sum(axis=1), axis=0).fillna(0)
        frame_all = (frame.fillna(0) * w.reindex(columns=frame.columns).fillna(0)).sum(axis=1)
        wsum = w.reindex(columns=frame.columns).fillna(0).sum(axis=1)
        frame_all = frame_all.where(wsum > 0)
        for plat in frame.columns:
            for d, v in frame[plat].dropna().items():
                out.append({"d": d.date(), "level": int(level), "code": code, "platform": plat, "index_level": float(v),
                            "weight": float(w.at[d, plat]) if plat in w.columns else None})
        for d, v in frame_all.dropna().items():
            out.append({"d": d.date(), "level": int(level), "code": code, "platform": "ALL", "index_level": float(v),
                        "weight": 1.0})
    return pd.DataFrame(out)


def platform_lead(con, cfg) -> pd.DataFrame:
    w = con.execute("""SELECT CAST(date_trunc('week', d) AS DATE) AS week, platform, code, sum(a) AS a
                       FROM platform_daily GROUP BY ALL HAVING count(DISTINCT d) = 7""").df()
    rows = []
    max_lag = min(int(cfg["later"]["lead_lag_max_weeks"]), 8)
    for code, g in w.groupby("code"):
        plats = sorted(g["platform"].unique())
        for lead in plats:
            for follow in plats:
                if lead == follow:
                    continue
                x = g[g["platform"] == lead].set_index(pd.to_datetime(g.loc[g["platform"] == lead, "week"]))["a"]
                y = g[g["platform"] == follow].set_index(pd.to_datetime(g.loc[g["platform"] == follow, "week"]))["a"]
                curve = [r for r in lead_lag_curve(x, y, max_lag) if r[0] > 0]
                if curve:
                    lag, rho, n = max(curve, key=lambda r: r[1])
                    rows.append({"code": code, "leader": lead, "follower": follow, "best_lag_weeks": lag, "rho": rho, "n": n})
    return pd.DataFrame(rows)


SCHEMAS = {
    "topic_ci": {"d": "DATE", "level": "INTEGER", "code": "VARCHAR", "wow": "DOUBLE", "wow_low": "DOUBLE",
                 "wow_high": "DOUBLE", "creators": "INTEGER", "samples": "INTEGER"},
    "lead_lag": {"series_id": "VARCHAR", "code": "VARCHAR", "source": "VARCHAR", "lag_weeks": "INTEGER",
                 "rho": "DOUBLE", "n": "INTEGER", "is_best": "BOOLEAN"},
    "predictive_tests": {"series_id": "VARCHAR", "code": "VARCHAR", "source": "VARCHAR", "lead_weeks": "INTEGER",
                         "weeks": "INTEGER", "lags": "INTEGER", "f_stat": "DOUBLE", "p_value": "DOUBLE",
                         "rmse_own_history": "DOUBLE", "rmse_with_attention": "DOUBLE",
                         "out_of_sample_better": "BOOLEAN", "passes": "BOOLEAN"},
    "call_scores": {"call_id": "INTEGER", "made_on": "DATE", "subject": "VARCHAR", "direction": "VARCHAR",
                    "horizon_weeks": "INTEGER", "series_id": "VARCHAR", "probability": "DOUBLE",
                    "manual_outcome": "VARCHAR", "status": "VARCHAR", "typical_move": "DOUBLE",
                    "lead_days": "DOUBLE", "brier": "DOUBLE", "base_hit_rate": "DOUBLE"},
    "call_summary": {"calls": "INTEGER", "resolved": "INTEGER", "hits": "INTEGER", "hit_rate": "DOUBLE",
                     "median_lead_days": "DOUBLE", "brier": "DOUBLE", "base_hit_rate": "DOUBLE",
                     "beat_base_rate": "DOUBLE"},
    "composite_index": {"d": "DATE", "level": "INTEGER", "code": "VARCHAR", "platform": "VARCHAR",
                        "index_level": "DOUBLE", "weight": "DOUBLE"},
    "platform_lead": {"code": "VARCHAR", "leader": "VARCHAR", "follower": "VARCHAR", "best_lag_weeks": "INTEGER",
                      "rho": "DOUBLE", "n": "INTEGER"},
}
TABLES = list(SCHEMAS)


def build(con, cfg: dict | None = None, calls_path: Path = calls_mod.CALLS, res_path: Path = calls_mod.RESOLUTIONS) -> None:
    cfg = cfg or formulas.config()
    scores = call_scores(con, cfg, calls_path, res_path)
    for name, df in [("topic_ci", topic_ci(con, cfg)), ("lead_lag", lead_lag(con, cfg)),
                     ("predictive_tests", predictive_tests(con, cfg)), ("call_scores", scores),
                     ("call_summary", call_summary(scores)), ("composite_index", composite_index(con)),
                     ("platform_lead", platform_lead(con, cfg))]:
        _write(con, name, df, SCHEMAS[name])
