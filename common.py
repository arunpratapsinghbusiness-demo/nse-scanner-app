"""
Shared config + helpers for the NSE gap-fill scanner.
Used by both train_model.py (weekly, heavy) and daily_scan.py (daily, light).
"""
import pandas as pd

# ---------------- Strategy config (same values as your notebook) ----------------
GAP_MIN_PCT = 0.3
GAP_MAX_PCT = 2.5
SL_BUFFER_GF = 3
RR_GF = 1.5
ATR_PERIOD = 14
BROKERAGE = 9.0
ML_THRESHOLD = 0.70

MIN_TRADES_FOR_RELIABILITY = 15
MIN_WIN_RATE = 85

# Position sizing (edit these to match your real capital)
ACCOUNT_BALANCE = 5000
LEVERAGE = 5
RISK_PCT_PER_TRADE = 4.0
MAX_EXPOSURE = ACCOUNT_BALANCE * LEVERAGE
RISK_AMOUNT_RS = ACCOUNT_BALANCE * (RISK_PCT_PER_TRADE / 100)

MAX_SIGNALS_PER_DAY = 3  # kitne top signals daily bhejne hain

# ---------------- Universe ----------------
# Curated "fundamentally strong" universe (from your notebook).
# NSE's full nifty500 CSV endpoint often blocks automated/cloud IPs (anti-bot),
# so for a free, unattended GitHub Actions job this fixed list is far more
# reliable than the dynamic NSE download every run.
FUNDAMENTALLY_STRONG_UNIVERSE = [
    "HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS",
    "TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS",
    "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS",
    "MARUTI.NS", "M&M.NS", "EICHERMOT.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS",
    "ULTRACEMCO.NS", "SHREECEM.NS", "GRASIM.NS", "LT.NS",
    "SUNPHARMA.NS", "DIVISLAB.NS", "CIPLA.NS", "DRREDDY.NS", "APOLLOHOSP.NS",
    "TITAN.NS", "ASIANPAINT.NS", "PIDILITIND.NS", "TRENT.NS",
    "BHARTIARTL.NS",
    "PAGEIND.NS", "HAVELLS.NS", "SIEMENS.NS", "ABB.NS", "PERSISTENT.NS",
    "MPHASIS.NS", "POLYCAB.NS", "CUMMINSIND.NS", "SBILIFE.NS", "HDFCLIFE.NS",
]

FEATURES = ["gap_pct", "atr", "vol_ratio", "dow", "risk"]


def compute_atr(df, period=ATR_PERIOD):
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def flatten_columns(df):
    """yfinance sometimes returns MultiIndex columns — flatten them."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def build_signal_from_first_bar(ticker, prev_close, first_bar, atr_val, dow):
    """
    Given the first 15-min bar of the day, build a LONG or SHORT gap-fill
    signal exactly like the notebook's backtest_gapfill() rule.

    gap UP   -> SHORT (sell)  -> price expected to fall back toward prev close
    gap DOWN -> LONG  (buy)   -> price expected to rise back toward prev close
    """
    gap_pct = (first_bar["Open"] - prev_close) / prev_close * 100
    if not (GAP_MIN_PCT <= abs(gap_pct) <= GAP_MAX_PCT):
        return None

    entry = first_bar["Close"]
    vol_ratio = 1.0  # first bar of day: expanding-mean-of-itself == itself, ratio is always 1.0
    # (this matches exactly how the training data was built, so the model sees the same feature)

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

    return {
        "ticker": ticker,
        "dir": direction,
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "risk": float(risk),
        "gap_pct": float(gap_pct),
        "atr": float(atr_val),
        "vol_ratio": float(vol_ratio),
        "dow": int(dow),
    }
