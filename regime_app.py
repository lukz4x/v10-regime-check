#!/usr/bin/env python3
"""
TQQQ D_rising_sma30 Daily Decision Helper (Yahoo Finance edition)
=================================================================

Companion script to the D_rising_sma30 Implementation Manual.

What it does:
  1. Fetches TQQQ history from Yahoo Finance (~5 years of daily bars)
  2. Computes all strategy indicators from the full history
  3. Asks you for your current position (lockout state, NLV, shares)
  4. Applies the decision tree and outputs tomorrow's action

Usage:
    python tqqq_daily_decision.py

    # Use a different ticker for testing (e.g. paper-trade on a substitute)
    python tqqq_daily_decision.py --ticker TQQQ

    # Fall back to a local CSV if Yahoo is unreachable
    python tqqq_daily_decision.py --csv path/to/tqqq_history.csv

Dependencies:
    pip install yfinance pandas numpy

Note: yfinance pulls the most-recent bar based on whatever Yahoo has cached.
During market hours, that "today" bar may be an intraday snapshot rather than
an official close. Run this script AFTER 4:00 PM ET to make sure today's
close is final.
"""

import argparse
import os
import sys
from datetime import date, timedelta
import pandas as pd
import numpy as np

# ============================================================================
# CONFIGURATION (these mirror the strategy spec — do not change without a reason)
# ============================================================================
DEFAULT_TICKER = "TQQQ"
LOOKBACK_PERIOD = "5y"   # Yahoo period string; 5y gives ~1260 trading days

VOL_TARGET = 50.0          # Vol target divisor: target_weight = min(50 / RV20, 1.0)
Z_LOOKBACK = 504           # Trading days (~2 years) for z-score rolling stats
SMA200_PERIOD = 200        # Long-term moving average for extension
SMA30_PERIOD = 30          # Momentum re-entry MA
SMA30_LAGGED_DAYS = 5      # "5 days ago" lag for momentum check
MIN_DAYS_LOCKED = 5        # Lockout must persist this long before momentum release
HIGH60_PERIOD = 60         # 60-day high for diagnostics
STOP_PCT = 0.08            # 8% same-day crash stop
REBALANCE_BAND = 0.02      # 2 percentage point rebalance band
Z_MULTIPLIER = 2.0         # Z-threshold = mean + 2 stdev


# ============================================================================
# DATA FETCHING
# ============================================================================

def fetch_from_yahoo(ticker, period=LOOKBACK_PERIOD):
    """
    Fetch daily adjusted close data from Yahoo Finance.
    Returns a DataFrame with 'date' and 'close' columns.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("ERROR: yfinance is not installed. Run:  pip install yfinance")
        sys.exit(1)

    print(f"Fetching {ticker} history from Yahoo Finance (period={period})...")
    try:
        # auto_adjust=True returns split-and-dividend-adjusted prices in 'Close'.
        # Use Ticker.history() to get flat (non-MultiIndex) columns for a single ticker.
        df = yf.Ticker(ticker).history(period=period, auto_adjust=True, actions=False)
    except Exception as e:
        print(f"ERROR fetching from Yahoo: {type(e).__name__}: {e}")
        sys.exit(1)

    if df is None or df.empty:
        print(f"ERROR: Yahoo returned no data for {ticker}. Try again later or use --csv fallback.")
        sys.exit(1)

    df = df.reset_index()
    # Normalize column names (yfinance may return 'Date' or 'Datetime')
    df.columns = [str(c).strip() for c in df.columns]
    date_col = next((c for c in df.columns if c.lower() in ('date', 'datetime', 'index')), None)
    close_col = next((c for c in df.columns if c.lower() == 'close'), None)
    if not date_col or not close_col:
        print(f"ERROR: Unexpected Yahoo response. Columns: {list(df.columns)}")
        sys.exit(1)

    df = df[[date_col, close_col]].copy()
    df.columns = ['date', 'close']
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None).dt.normalize()
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df = df.dropna().sort_values('date').reset_index(drop=True)
    df = df.drop_duplicates(subset=['date'], keep='last')
    return df


def load_from_csv(csv_path):
    """Fallback: load history from local CSV. Same format as the Yahoo data."""
    if not os.path.exists(csv_path):
        print(f"ERROR: CSV not found at {csv_path}")
        sys.exit(1)
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    date_col = next((c for c in df.columns if c.upper() in ('DATE', 'TIMESTAMP', 'TIME', 'T')), None)
    close_col = next((c for c in df.columns if c.upper() in ('TQQQ', 'CLOSE', 'PRICE', 'C')), None)
    if not date_col or not close_col:
        print(f"ERROR: CSV needs DATE and CLOSE (or TQQQ) columns. Found: {list(df.columns)}")
        sys.exit(1)
    df = df[[date_col, close_col]].copy()
    df.columns = ['date', 'close']
    df['date'] = pd.to_datetime(df['date']).dt.normalize()
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df = df.dropna().sort_values('date').reset_index(drop=True)
    df = df.drop_duplicates(subset=['date'], keep='last')
    return df


# ============================================================================
# INDICATOR COMPUTATION
# ============================================================================

def compute_indicators(df):
    """Compute all strategy indicators on a price series."""
    d = df.copy()
    d['ret'] = d['close'].pct_change()
    d['rv20'] = d['ret'].rolling(20).std(ddof=1) * np.sqrt(252) * 100
    d['sma30'] = d['close'].rolling(SMA30_PERIOD).mean()
    d['sma30_lagged'] = d['sma30'].shift(SMA30_LAGGED_DAYS)
    d['sma200'] = d['close'].rolling(SMA200_PERIOD).mean()
    d['ext'] = (d['close'] / d['sma200'] - 1) * 100
    d['high60'] = d['close'].rolling(HIGH60_PERIOD).max()
    d['new_high'] = d['close'] >= 0.999 * d['high60']
    d['ext_mean_504'] = d['ext'].rolling(Z_LOOKBACK).mean()
    d['ext_std_504'] = d['ext'].rolling(Z_LOOKBACK).std(ddof=1)
    d['z_threshold'] = d['ext_mean_504'] + Z_MULTIPLIER * d['ext_std_504']
    d['z_score'] = (d['ext'] - d['ext_mean_504']) / d['ext_std_504']
    return d


# ============================================================================
# USER STATE PROMPT
# ============================================================================

def prompt_state():
    """Ask the user for the state they recorded yesterday."""
    print("\n" + "=" * 64)
    print(" CURRENT STATE — what you tracked yesterday")
    print("=" * 64)

    locked = input("Are you currently in lockout? [y/N]: ").strip().lower() == 'y'

    days_locked = 0
    if locked:
        while True:
            try:
                days_locked = int(input("Days already in lockout (integer): "))
                if days_locked < 0:
                    print("Must be non-negative.")
                    continue
                break
            except ValueError:
                print("Invalid integer, try again.")

    while True:
        try:
            raw = input("Current account NLV ($): ").strip().replace('$', '').replace(',', '')
            nlv = float(raw)
            break
        except ValueError:
            print("Invalid number, try again.")

    while True:
        try:
            raw = input("Current TQQQ shares (0 if in cash): ").strip().replace(',', '')
            shares = float(raw)
            break
        except ValueError:
            print("Invalid number, try again.")

    return {'locked': locked, 'days_locked': days_locked, 'nlv': nlv, 'shares': shares}


# ============================================================================
# DECISION TREE — D_rising_sma30
# ============================================================================

def make_decision(row, state):
    """
    Apply the D_rising_sma30 state machine using today's close indicators.

    Returns:
        (new_locked, new_days_locked, target_weight, notes)
    """
    rv20 = row['rv20']
    ext = row['ext']
    sma30 = row['sma30']
    sma30_lagged = row['sma30_lagged']
    z_thresh = row['z_threshold']
    close = row['close']

    locked = state['locked']
    days_locked = state['days_locked']
    notes = []

    if not locked:
        if ext >= z_thresh:
            locked = True
            days_locked = 0
            notes.append(f"LOCKOUT TRIGGER: ext +{ext:.2f}% >= z_threshold +{z_thresh:.2f}%")
            target = 0.0
        else:
            target = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
            notes.append(f"No lockout. Vol-target = min(50 / {rv20:.2f}, 1.0) = {target:.3f}")
    else:
        days_locked += 1
        notes.append(f"Already locked. Day {days_locked} of lockout.")

        # Primary release (ext returns to/below SMA200)
        if ext <= 0:
            locked = False
            days_locked = 0
            target = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
            notes.append(f"PRIMARY RELEASE: ext {ext:.2f}% <= 0. Re-engage at vol-target = {target:.3f}.")
        # Momentum release (D_rising_sma30)
        elif (days_locked >= MIN_DAYS_LOCKED
              and sma30 > sma30_lagged
              and close > sma30
              and ext < z_thresh):
            locked = False
            days_locked = 0
            target = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
            notes.append(
                f"MOMENTUM RELEASE: SMA30 rising AND price > SMA30 AND ext < threshold. "
                f"Re-engage at vol-target = {target:.3f}."
            )
        else:
            target = 0.0
            blocks = []
            if days_locked < MIN_DAYS_LOCKED:
                blocks.append(f"days_locked < {MIN_DAYS_LOCKED} (have {days_locked})")
            if sma30 <= sma30_lagged:
                blocks.append(f"SMA30 not rising (${sma30:.2f} vs ${sma30_lagged:.2f} 5d ago)")
            if close <= sma30:
                blocks.append(f"price not > SMA30 (${close:.2f} vs ${sma30:.2f})")
            if ext >= z_thresh:
                blocks.append(f"ext not < threshold (+{ext:.2f}% vs +{z_thresh:.2f}%)")
            notes.append(f"NO RELEASE — blocked by: {'; '.join(blocks)}")

    return locked, days_locked, target, notes


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="TQQQ D_rising_sma30 daily decision helper")
    parser.add_argument('--ticker', default=DEFAULT_TICKER,
                        help="Ticker to fetch (default: TQQQ)")
    parser.add_argument('--csv', default=None,
                        help="Fallback: path to a local CSV with DATE and CLOSE columns")
    parser.add_argument('--period', default=LOOKBACK_PERIOD,
                        help="Yahoo lookback period (default: 5y)")
    args = parser.parse_args()

    print("\n" + "=" * 64)
    print(" D_rising_sma30 Daily Decision Helper")
    print(" TQQQ paper-trade operational tool")
    print("=" * 64)

    if args.csv:
        print(f"\nLoading {args.ticker} history from CSV: {args.csv}")
        df = load_from_csv(args.csv)
    else:
        df = fetch_from_yahoo(args.ticker, period=args.period)

    print(f"Loaded {len(df)} bars from {df['date'].iloc[0].date()} "
          f"to {df['date'].iloc[-1].date()}.")

    # Warn if last bar isn't very recent
    last_date = df['date'].iloc[-1].date()
    today = date.today()
    gap = (today - last_date).days
    if gap > 4:
        print(f"\nWARNING: Last bar is {gap} calendar days old "
              f"(last: {last_date}, today: {today}).")
        print("Yahoo may not have updated yet, or you're running before market close.")
        ans = input("Proceed with this data? [y/N]: ").strip().lower()
        if ans != 'y':
            sys.exit(0)
    elif gap == 0:
        print(f"Today's bar is present (good — run this script AFTER 4 PM ET to be safe).")

    d = compute_indicators(df)
    last = d.iloc[-1]

    if pd.isna(last['ext_mean_504']):
        print(f"\nWARNING: Not enough history for 504-day rolling stats.")
        print(f"Need at least {SMA200_PERIOD + Z_LOOKBACK} bars. Have {len(d)}.")
        print(f"Increase --period (e.g. --period 10y) or extend the CSV history.")
        sys.exit(1)

    # Show today's indicators
    print(f"\n=== Indicators at {last['date'].date()} close ===")
    print(f"  Close            ${last['close']:.2f}")
    print(f"  RV20             {last['rv20']:.2f}%")
    print(f"  SMA30            ${last['sma30']:.2f}")
    print(f"  SMA30 (5d ago)   ${last['sma30_lagged']:.2f}   "
          f"(rising? {'YES' if last['sma30'] > last['sma30_lagged'] else 'NO'})")
    print(f"  SMA200           ${last['sma200']:.2f}")
    print(f"  Extension        {last['ext']:+.2f}%")
    print(f"  60-day high      ${last['high60']:.2f}   "
          f"(at new high? {'YES' if last['new_high'] else 'NO'})")
    print(f"  Z-threshold      {last['z_threshold']:+.2f}%")
    print(f"  Z-score          {last['z_score']:+.2f} sigma")

    # Prompt state
    state = prompt_state()

    # Apply decision tree
    new_locked, new_days, target, notes = make_decision(last, state)

    # Compute action
    nlv = state['nlv']
    cur_shares = state['shares']
    cur_value = cur_shares * last['close']
    cur_weight = cur_value / nlv if nlv > 0 else 0.0

    if abs(target - cur_weight) < REBALANCE_BAND:
        action_weight = cur_weight
        rebalance_note = f"Within {REBALANCE_BAND*100:.0f}pp rebalance band — no trade."
    else:
        action_weight = target
        rebalance_note = "Outside rebalance band — trade required."

    target_value = nlv * action_weight
    target_shares = int(target_value / last['close'])
    shares_to_trade = target_shares - int(cur_shares)

    # Output
    print(f"\n" + "=" * 64)
    print(f" DECISION (action for next trading day)")
    print(f"=" * 64)
    for n in notes:
        print(f"  - {n}")

    print(f"\n  New lockout state:    {'LOCKED' if new_locked else 'ENGAGED'}")
    if new_locked:
        print(f"  Days locked tomorrow: {new_days}")
    print(f"  Target weight:        {action_weight*100:.1f}%")
    print(f"  Current weight:       {cur_weight*100:.1f}%")
    print(f"  {rebalance_note}")

    if shares_to_trade != 0:
        side = "BUY" if shares_to_trade > 0 else "SELL"
        print(f"\n  ACTION: {side} {abs(shares_to_trade)} shares of "
              f"{args.ticker} at next open (MOO).")
        print(f"          Target position: {target_shares} shares "
              f"(~${target_value:,.0f} at today's close).")
    else:
        print(f"\n  ACTION: No trade. Hold {int(cur_shares)} shares.")

    if action_weight > 0:
        stop_price = last['close'] * (1 - STOP_PCT)
        print(f"\n  STOP: After your fill, place a stop-MARKET sell at ${stop_price:.2f}")
        print(f"        (8% below today's close; cancel at 3:55 PM ET each day).")

    # Memo for tomorrow
    print(f"\n=== Record this for tomorrow's session ===")
    print(f"  Date executed:      {last['date'].date()}")
    print(f"  Locked:             {new_locked}")
    print(f"  Days locked:        {new_days}")
    print(f"  Target weight:      {action_weight*100:.1f}%")
    print(f"  Shares (after fill): {target_shares}")
    print()


if __name__ == '__main__':
    main()
