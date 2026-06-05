"""
TQQQ D_rising_sma30 Daily Decision Helper — Streamlit App
==========================================================

Deploys to Streamlit Community Cloud (streamlit.io).

Run locally:
    pip install -r requirements.txt
    streamlit run streamlit_app.py

Strategy:
    Long-only TQQQ position, vol-targeted at min(50 / RV20, 1.0),
    with z-score lockout trigger and SMA30 momentum re-entry.
"""

import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

# ============================================================================
# STRATEGY CONFIGURATION
# ============================================================================
DEFAULT_TICKER = "TQQQ"
LOOKBACK_PERIOD = "5y"

VOL_TARGET = 50.0
Z_LOOKBACK = 504
SMA200_PERIOD = 200
SMA30_PERIOD = 30
SMA30_LAGGED_DAYS = 5
MIN_DAYS_LOCKED = 5
HIGH60_PERIOD = 60
STOP_PCT = 0.08
REBALANCE_BAND = 0.02
Z_MULTIPLIER = 2.0


# ============================================================================
# DATA + INDICATORS
# ============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_history(ticker: str, period: str) -> pd.DataFrame:
    """Fetch daily adjusted closes from Yahoo Finance. Cached for 1 hour."""
    df = yf.Ticker(ticker).history(period=period, auto_adjust=True, actions=False)
    if df is None or df.empty:
        raise RuntimeError(f"Yahoo returned no data for {ticker}")
    df = df.reset_index()
    df.columns = [str(c).strip() for c in df.columns]
    date_col = next((c for c in df.columns if c.lower() in ("date", "datetime", "index")), None)
    close_col = next((c for c in df.columns if c.lower() == "close"), None)
    if not date_col or not close_col:
        raise RuntimeError(f"Unexpected Yahoo response. Columns: {list(df.columns)}")
    out = df[[date_col, close_col]].copy()
    out.columns = ["date", "close"]
    out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None).dt.normalize()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna().sort_values("date").reset_index(drop=True)
    out = out.drop_duplicates(subset=["date"], keep="last")
    return out


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ret"] = d["close"].pct_change()
    d["rv20"] = d["ret"].rolling(20).std(ddof=1) * np.sqrt(252) * 100
    d["sma30"] = d["close"].rolling(SMA30_PERIOD).mean()
    d["sma30_lagged"] = d["sma30"].shift(SMA30_LAGGED_DAYS)
    d["sma200"] = d["close"].rolling(SMA200_PERIOD).mean()
    d["ext"] = (d["close"] / d["sma200"] - 1) * 100
    d["high60"] = d["close"].rolling(HIGH60_PERIOD).max()
    d["new_high"] = d["close"] >= 0.999 * d["high60"]
    d["ext_mean_504"] = d["ext"].rolling(Z_LOOKBACK).mean()
    d["ext_std_504"] = d["ext"].rolling(Z_LOOKBACK).std(ddof=1)
    d["z_threshold"] = d["ext_mean_504"] + Z_MULTIPLIER * d["ext_std_504"]
    d["z_score"] = (d["ext"] - d["ext_mean_504"]) / d["ext_std_504"]
    return d


# ============================================================================
# DECISION TREE
# ============================================================================
def make_decision(row: pd.Series, locked: bool, days_locked: int):
    """Apply D_rising_sma30 state machine. Returns (new_locked, new_days, target_weight, notes)."""
    rv20 = row["rv20"]
    ext = row["ext"]
    sma30 = row["sma30"]
    sma30_lagged = row["sma30_lagged"]
    z_thresh = row["z_threshold"]
    close = row["close"]
    notes = []

    if not locked:
        if ext >= z_thresh:
            return True, 0, 0.0, [
                f"LOCKOUT TRIGGER: extension {ext:+.2f}% ≥ z-threshold {z_thresh:+.2f}%"
            ]
        target = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
        return False, 0, target, [
            f"Engaged. Vol-target = min(50 / {rv20:.2f}, 1.0) = {target:.3f}"
        ]

    # Already locked
    days_locked += 1
    notes.append(f"Already locked. Day {days_locked} of lockout.")

    # Primary release
    if ext <= 0:
        target = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
        notes.append(f"PRIMARY RELEASE: ext {ext:.2f}% ≤ 0. Re-engage at vol-target = {target:.3f}.")
        return False, 0, target, notes

    # Momentum release
    if (days_locked >= MIN_DAYS_LOCKED
            and sma30 > sma30_lagged
            and close > sma30
            and ext < z_thresh):
        target = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
        notes.append(
            f"MOMENTUM RELEASE: SMA30 rising, price > SMA30, ext < threshold. "
            f"Re-engage at vol-target = {target:.3f}."
        )
        return False, 0, target, notes

    # Still locked — explain why
    blocks = []
    if days_locked < MIN_DAYS_LOCKED:
        blocks.append(f"days_locked < {MIN_DAYS_LOCKED} (have {days_locked})")
    if sma30 <= sma30_lagged:
        blocks.append(f"SMA30 not rising (${sma30:.2f} vs ${sma30_lagged:.2f} 5d ago)")
    if close <= sma30:
        blocks.append(f"price not > SMA30 (${close:.2f} vs ${sma30:.2f})")
    if ext >= z_thresh:
        blocks.append(f"ext not < threshold ({ext:+.2f}% vs {z_thresh:+.2f}%)")
    notes.append("NO RELEASE — blocked by: " + "; ".join(blocks))
    return True, days_locked, 0.0, notes


# ============================================================================
# UI
# ============================================================================
st.set_page_config(
    page_title="TQQQ D_rising_sma30",
    page_icon="📈",
    layout="wide"
)

st.title("📈 TQQQ D_rising_sma30 Daily Decision")
st.caption(
    "Volatility-targeted long TQQQ with z-score lockout, SMA30 momentum re-entry, "
    "and 8% same-day crash stop."
)

# Sidebar controls
with st.sidebar:
    st.header("Settings")
    ticker = st.text_input("Ticker", value=DEFAULT_TICKER).strip().upper()
    period = st.selectbox("History lookback", ["5y", "10y", "max"], index=0)
    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.caption(
        "Data: Yahoo Finance via yfinance. Cache TTL: 1 hour. "
        "Run after 4 PM ET so today's close is final."
    )

# Fetch
try:
    with st.spinner(f"Fetching {ticker} history..."):
        df = fetch_history(ticker, period)
except Exception as e:
    st.error(f"Failed to fetch {ticker}: {e}")
    st.stop()

d = compute_indicators(df)
last = d.iloc[-1]

if pd.isna(last["ext_mean_504"]):
    st.error(
        f"Not enough history for 504-day rolling stats. "
        f"Need ~{SMA200_PERIOD + Z_LOOKBACK} bars; have {len(d)}. "
        f"Try a longer lookback."
    )
    st.stop()

# Date freshness warning
last_date = last["date"].date()
today = date.today()
gap_days = (today - last_date).days
if gap_days > 4:
    st.warning(
        f"Last bar is {gap_days} calendar days old (last: {last_date}, today: {today}). "
        f"Yahoo may not have updated, or you're running on a weekend/holiday."
    )

st.subheader(f"Market indicators — {last_date}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Close", f"${last['close']:.2f}")
c2.metric("RV20", f"{last['rv20']:.2f}%")
c3.metric("Extension", f"{last['ext']:+.2f}%")
c4.metric("Z-score", f"{last['z_score']:+.2f}σ")

c5, c6, c7, c8 = st.columns(4)
c5.metric("SMA30", f"${last['sma30']:.2f}",
          delta=f"{'rising' if last['sma30'] > last['sma30_lagged'] else 'falling'} vs 5d ago",
          delta_color="normal")
c6.metric("SMA200", f"${last['sma200']:.2f}")
c7.metric("Z-threshold", f"{last['z_threshold']:+.2f}%",
          delta=f"{last['ext'] - last['z_threshold']:+.2f}pp from ext",
          delta_color="inverse")
c8.metric("60d high", f"${last['high60']:.2f}",
          delta="at new high" if last['new_high'] else "below high",
          delta_color="off")

# Chart of extension vs z_threshold over the last 2 years
with st.expander("📊 Extension vs Z-threshold history", expanded=False):
    chart_df = d[["date", "ext", "z_threshold"]].dropna().tail(504).set_index("date")
    chart_df.columns = ["Extension %", "Z-threshold %"]
    st.line_chart(chart_df)
    st.caption(
        "When the blue line (extension) crosses above the orange line (z-threshold), "
        "the lockout fires."
    )

st.divider()

# State input
st.subheader("Your current position")
sc1, sc2, sc3, sc4 = st.columns([1, 1, 1, 1])
with sc1:
    locked = st.checkbox("Currently in lockout?", value=False)
with sc2:
    days_locked = st.number_input(
        "Days already locked", min_value=0, max_value=500, value=0, step=1,
        disabled=not locked,
        help="The count of trading days since the lockout fired (set to 0 if not locked)."
    )
with sc3:
    nlv = st.number_input("Account NLV ($)", min_value=0.0, value=100000.0, step=1000.0, format="%.2f")
with sc4:
    shares = st.number_input("Current TQQQ shares", min_value=0, value=0, step=1)

st.divider()

# Decision
new_locked, new_days, target, notes = make_decision(
    last,
    locked=locked,
    days_locked=int(days_locked)
)

cur_value = shares * last["close"]
cur_weight = cur_value / nlv if nlv > 0 else 0.0

if abs(target - cur_weight) < REBALANCE_BAND:
    action_weight = cur_weight
    in_band = True
else:
    action_weight = target
    in_band = False

target_value = nlv * action_weight
target_shares = int(target_value / last["close"])
shares_to_trade = target_shares - int(shares)

st.subheader("Decision")

# Big colored card with the headline action
if new_locked and shares_to_trade == 0 and shares == 0:
    st.error(f"🔒 **LOCKED** — Hold cash. Day {new_days} of lockout.")
elif new_locked and shares > 0:
    st.error(f"🔒 **LOCKED — SELL {abs(shares_to_trade)} shares at next open (MOO).** Lockout active.")
elif not new_locked and shares_to_trade > 0:
    st.success(
        f"🟢 **ENGAGED — BUY {abs(shares_to_trade)} shares at next open (MOO).** "
        f"Target weight {target*100:.1f}%."
    )
elif not new_locked and shares_to_trade < 0:
    st.warning(
        f"🟡 **ENGAGED — SELL {abs(shares_to_trade)} shares to rebalance.** "
        f"Target weight {target*100:.1f}%."
    )
else:
    st.info(f"⚪ **HOLD** — No trade needed. Target {target*100:.1f}% ≈ current {cur_weight*100:.1f}%.")

# Detailed breakdown
dc1, dc2, dc3 = st.columns(3)
dc1.metric("Target weight (tomorrow)", f"{action_weight*100:.1f}%",
           delta=f"{(action_weight - cur_weight)*100:+.1f}pp")
dc2.metric("Current weight", f"{cur_weight*100:.1f}%",
           delta=f"${cur_value:,.0f} position")
dc3.metric("Lockout state (after today)",
           "LOCKED" if new_locked else "ENGAGED",
           delta=f"day {new_days}" if new_locked else None,
           delta_color="off")

# Notes / reasoning
st.markdown("**Decision tree evaluation:**")
for n in notes:
    st.markdown(f"- {n}")

if in_band and shares_to_trade == 0:
    st.caption(f"💡 Within {REBALANCE_BAND*100:.0f}pp rebalance band — no trade required.")

# Stop price
if action_weight > 0:
    stop_price = last["close"] * (1 - STOP_PCT)
    st.info(
        f"🛡️ **Stop order**: After your buy fills, place a **stop-MARKET sell at "
        f"${stop_price:.2f}** (8% below today's close). Cancel at 3:55 PM ET — intraday only."
    )

st.divider()

# What to record
with st.expander("📝 State to record for tomorrow's session", expanded=False):
    record = pd.DataFrame([{
        "Date executed": str(last_date),
        "Close": f"${last['close']:.2f}",
        "RV20 %": f"{last['rv20']:.2f}",
        "Ext %": f"{last['ext']:+.2f}",
        "Z-threshold %": f"{last['z_threshold']:+.2f}",
        "Z-score σ": f"{last['z_score']:+.2f}",
        "Locked tomorrow": new_locked,
        "Days locked tomorrow": new_days,
        "Target weight %": f"{action_weight*100:.1f}",
        "Shares after fill": target_shares,
    }])
    st.dataframe(record.T.rename(columns={0: "value"}), use_container_width=True)

# Footer
st.caption(
    f"📉 Strategy: D_rising_sma30. Data through {last_date}. "
    f"Cached fetch refreshes every 60 min — use ⟳ Refresh in the sidebar to force-reload."
)
