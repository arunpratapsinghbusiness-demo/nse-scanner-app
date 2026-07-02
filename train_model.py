"""
WEEKLY job (runs via GitHub Actions cron, e.g. every Sunday night).
Heavy step: downloads 60 days of 15-min data for the whole universe,
backtests the gap-fill rule, trains the ML model, and saves:
  - docs/data/model.pkl        (trained GradientBoostingClassifier)
  - docs/data/watchlist.json   (top picks + their backtested stats)

daily_scan.py (the fast, market-hours job) just LOADS these files —
it never retrains. That's what keeps the 10:00 AM job fast and reliable.
"""
import json
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score

from common import (
    ATR_PERIOD, BROKERAGE, FEATURES, FUNDAMENTALLY_STRONG_UNIVERSE,
    ML_THRESHOLD, MIN_TRADES_FOR_RELIABILITY, MIN_WIN_RATE, RR_GF,
    GAP_MIN_PCT, GAP_MAX_PCT, SL_BUFFER_GF, compute_atr, flatten_columns,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def backtest_gapfill(ticker):
    try:
        df = yf.download(ticker, period="60d", interval="15m",
                          progress=False, auto_adjust=False)
        if df.empty:
            return pd.DataFrame()

        df = flatten_columns(df).reset_index()
        df["Date"] = df["Datetime"].dt.date
        df = df.sort_values("Datetime").reset_index(drop=True)
        df["ATR"] = compute_atr(df, ATR_PERIOD)
        df["RunningAvgVol"] = df.groupby("Date")["Volume"].transform(
            lambda x: x.expanding().mean())
        daily_close = df.groupby("Date")["Close"].last()
        dates = sorted(df["Date"].unique())
        trades = []

        for i in range(1, len(dates)):
            date, prev_date = dates[i], dates[i - 1]
            prev_close = daily_close.loc[prev_date]
            day_bars = df[df["Date"] == date].reset_index(drop=True)
            if day_bars.empty:
                continue
            first_bar = day_bars.loc[0]
            gap_pct = (first_bar["Open"] - prev_close) / prev_close * 100
            if not (GAP_MIN_PCT <= abs(gap_pct) <= GAP_MAX_PCT):
                continue

            entry = first_bar["Close"]
            atr_val = first_bar["ATR"]
            vol_ratio = first_bar["Volume"] / first_bar["RunningAvgVol"]
            dow = pd.Timestamp(date).dayofweek

            if gap_pct > 0:
                sl = first_bar["High"] + SL_BUFFER_GF
                risk = sl - entry
                tp = max(prev_close, entry - RR_GF * risk)
                direction = "SHORT"
            else:
                sl = first_bar["Low"] - SL_BUFFER_GF
                risk = entry - sl
                tp = min(prev_close, entry + RR_GF * risk)
                direction = "LONG"

            trades.append({"ticker": ticker, "date": date, "dir": direction,
                            "entry": entry, "sl": sl, "tp": tp, "risk": risk,
                            "gap_pct": gap_pct, "atr": atr_val,
                            "vol_ratio": vol_ratio, "dow": dow})

        tdf = pd.DataFrame(trades)
        if tdf.empty:
            return tdf

        outcomes = []
        for _, t in tdf.iterrows():
            forward = df[df["Date"] == t["date"]].iloc[1:81]
            outcome = "OPEN"
            for _, fb in forward.iterrows():
                if t["dir"] == "LONG":
                    if fb["Low"] <= t["sl"]: outcome = "LOSS"; break
                    if fb["High"] >= t["tp"]: outcome = "WIN"; break
                else:
                    if fb["High"] >= t["sl"]: outcome = "LOSS"; break
                    if fb["Low"] <= t["tp"]: outcome = "WIN"; break
            outcomes.append(outcome)
        tdf["outcome"] = outcomes
        return tdf[tdf["outcome"].isin(["WIN", "LOSS"])].copy()

    except Exception as e:
        print(f"{ticker} failed: {type(e).__name__}: {e}")
        return pd.DataFrame()


def main():
    all_trades = []
    stock_by_stock_results = []

    for ticker in FUNDAMENTALLY_STRONG_UNIVERSE:
        tdf = backtest_gapfill(ticker)
        if tdf.empty:
            print(f"{ticker:15s} | 0 trades -> SKIP")
            stock_by_stock_results.append(
                {"ticker": ticker, "n_trades": 0, "verdict": "NO_DATA"})
            time.sleep(0.5)
            continue

        all_trades.append(tdf)
        resolved = tdf.copy()
        resolved["pnl_gross"] = resolved.apply(
            lambda r: r["risk"] * RR_GF if r["outcome"] == "WIN" else -r["risk"], axis=1)
        resolved["pnl_net"] = resolved["pnl_gross"] - BROKERAGE
        win_rate = (resolved["outcome"] == "WIN").mean() * 100
        net_exp = resolved["pnl_net"].mean()
        verdict = "KEEP" if net_exp > 0 else "REMOVE"
        print(f"{ticker:15s} | {len(resolved):3d} trades | WR {win_rate:5.1f}% | "
              f"NetExp Rs.{net_exp:7.2f} | {verdict}")
        stock_by_stock_results.append({
            "ticker": ticker, "n_trades": len(resolved),
            "win_rate": round(win_rate, 1), "net_exp": round(net_exp, 2),
            "verdict": verdict,
        })
        time.sleep(0.5)  # be gentle with yfinance rate limits

    if not all_trades:
        raise SystemExit("No trades collected from any ticker — aborting (check data source).")

    raw_trades_df = pd.concat(all_trades, ignore_index=True)
    stock_df = pd.DataFrame(stock_by_stock_results)
    filtered_watchlist = stock_df[stock_df["verdict"] == "KEEP"]["ticker"].tolist()
    print(f"\nFiltered KEEP watchlist: {len(filtered_watchlist)} stocks")

    # ---- Train ML model ----
    ml_df = raw_trades_df[raw_trades_df["ticker"].isin(filtered_watchlist)].copy()
    ml_df = ml_df.dropna(subset=FEATURES)
    X, y = ml_df[FEATURES], (ml_df["outcome"] == "WIN").astype(int)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    model = GradientBoostingClassifier(
        n_estimators=100, max_depth=3, learning_rate=0.05,
        min_samples_leaf=20, random_state=42)
    scores = cross_val_score(model, X, y, cv=cv, scoring="accuracy")
    print(f"CV Accuracy: {scores.mean():.3f} (+/- {scores.std():.3f})")
    model.fit(X, y)
    ml_df["ml_prob"] = model.predict_proba(X)[:, 1]

    with open(OUT_DIR / "model.pkl", "wb") as f:
        pickle.dump(model, f)

    # ---- Final top-picks selection (same rule as notebook) ----
    final_stats = []
    for ticker, grp in ml_df.groupby("ticker"):
        approved = grp[grp["ml_prob"] >= ML_THRESHOLD].copy()
        if len(approved) < MIN_TRADES_FOR_RELIABILITY:
            continue
        approved["pnl_gross"] = approved.apply(
            lambda r: r["risk"] * RR_GF if r["outcome"] == "WIN" else -r["risk"], axis=1)
        approved["pnl_net"] = approved["pnl_gross"] - BROKERAGE
        win_rate = (approved["outcome"] == "WIN").mean() * 100
        net_exp = approved["pnl_net"].mean()
        final_stats.append({
            "ticker": ticker,
            "ml_trades": len(approved),
            "win_rate": round(win_rate, 1),
            "net_exp_per_trade": round(net_exp, 2),
            "avg_risk_pts": round(approved["risk"].mean(), 2),
            "avg_entry_price": round(approved["entry"].mean(), 2),
        })

    final_df = pd.DataFrame(final_stats).sort_values(
        ["win_rate", "net_exp_per_trade"], ascending=[False, False])
    top_picks = final_df[final_df["win_rate"] >= MIN_WIN_RATE]

    watchlist_payload = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "cv_accuracy": round(float(scores.mean()), 3),
        "filtered_watchlist": filtered_watchlist,
        "top_picks": top_picks.to_dict(orient="records"),
    }
    with open(OUT_DIR / "watchlist.json", "w") as f:
        json.dump(watchlist_payload, f, indent=2)

    print("\n=== TOP PICKS (used by daily_scan.py) ===")
    print(top_picks.to_string(index=False))
    print(f"\nSaved model.pkl + watchlist.json to {OUT_DIR}")


if __name__ == "__main__":
    main()
