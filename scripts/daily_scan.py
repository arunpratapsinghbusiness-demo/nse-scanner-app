"""
DAILY job (runs via GitHub Actions cron at ~10:00 AM IST).
Fast step: only fetches today's data for the pre-computed watchlist
(not the whole universe), predicts with the already-trained model,
does position sizing, and:
  - saves docs/data/signals.json  (read by the PWA)
  - sends a free push notification via ntfy.sh (works even if PWA is closed)
"""
import json
import os
import pickle
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from common import (
    ATR_PERIOD, MAX_EXPOSURE, MAX_SIGNALS_PER_DAY, ML_THRESHOLD,
    RISK_AMOUNT_RS, compute_atr, flatten_columns, build_signal_from_first_bar,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")  # set as a GitHub Actions secret


def load_model_and_watchlist():
    with open(DATA_DIR / "model.pkl", "rb") as f:
        model = pickle.load(f)
    with open(DATA_DIR / "watchlist.json") as f:
        watchlist = json.load(f)
    stats_by_ticker = {row["ticker"]: row for row in watchlist["top_picks"]}
    tickers = list(stats_by_ticker.keys())
    return model, stats_by_ticker, tickers


def fetch_today_signal(ticker):
    """Pull recent 15m data, build today's gap-fill signal (LONG or SHORT) if any."""
    df = yf.download(ticker, period="10d", interval="15m",
                      progress=False, auto_adjust=False)
    if df.empty:
        return None
    df = flatten_columns(df).reset_index()
    df["Date"] = df["Datetime"].dt.date
    df = df.sort_values("Datetime").reset_index(drop=True)
    df["ATR"] = compute_atr(df, ATR_PERIOD)

    dates = sorted(df["Date"].unique())
    if len(dates) < 2:
        return None
    today, prev_date = dates[-1], dates[-2]
    prev_close = df[df["Date"] == prev_date]["Close"].iloc[-1]
    today_bars = df[df["Date"] == today].reset_index(drop=True)
    if today_bars.empty:
        return None

    first_bar = today_bars.loc[0]
    if pd.isna(first_bar["ATR"]):
        return None  # not enough history yet for a 14-period ATR

    dow = pd.Timestamp(today).dayofweek
    return build_signal_from_first_bar(ticker, prev_close, first_bar, first_bar["ATR"], dow)


def position_size(signal, stats):
    avg_risk = signal["risk"]
    entry = signal["entry"]
    if avg_risk <= 0:
        return 0
    qty_by_risk = int(RISK_AMOUNT_RS / avg_risk)
    capital_needed = qty_by_risk * entry
    if capital_needed > MAX_EXPOSURE:
        return max(int(MAX_EXPOSURE / entry), 0)
    return max(qty_by_risk, 0)


def send_push(signals):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC secret not set — skipping push notification.")
        return
    if not signals:
        msg = "Aaj koi qualifying gap-fill signal nahi mila."
        title = "NSE Scanner — No signals today"
    else:
        lines = []
        for s in signals:
            lines.append(
                f"{s['dir']} {s['ticker'].replace('.NS','')} | Entry {s['entry']:.2f} "
                f"| SL {s['sl']:.2f} | Target {s['tp']:.2f} | Qty {s['qty']} "
                f"| Win% {s['win_rate']}"
            )
        msg = "\n".join(lines)
        title = f"NSE Scanner — {len(signals)} signal(s) ready"

    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode("utf-8"),
            headers={"Title": title, "Priority": "high", "Tags": "chart_with_upwards_trend"},
            timeout=15,
        )
        print("Push notification sent.")
    except Exception as e:
        print(f"Push notification failed: {e}")


def main():
    model, stats_by_ticker, tickers = load_model_and_watchlist()

    candidates = []
    for ticker in tickers:
        sig = fetch_today_signal(ticker)
        if sig is None:
            continue
        X = pd.DataFrame([[sig["gap_pct"], sig["atr"], sig["vol_ratio"], sig["dow"], sig["risk"]]],
                          columns=["gap_pct", "atr", "vol_ratio", "dow", "risk"])
        ml_prob = float(model.predict_proba(X)[0, 1])
        if ml_prob < ML_THRESHOLD:
            continue
        stats = stats_by_ticker[ticker]
        sig["ml_prob"] = round(ml_prob, 3)
        sig["win_rate"] = stats["win_rate"]
        sig["qty"] = position_size(sig, stats)
        candidates.append(sig)

    candidates.sort(key=lambda s: (s["win_rate"], s["ml_prob"]), reverse=True)
    top_signals = candidates[:MAX_SIGNALS_PER_DAY]

    payload = {
        "date": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "generated_at": pd.Timestamp.now().isoformat(),
        "signals": top_signals,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "signals.json", "w") as f:
        json.dump(payload, f, indent=2)

    print(f"Saved {len(top_signals)} signal(s) to signals.json")
    send_push(top_signals)


if __name__ == "__main__":
    main()
