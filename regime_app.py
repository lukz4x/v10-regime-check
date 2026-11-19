"""
TQQQ D_rising_sma30 Daily Decision Helper — Streamlit App
==========================================================

Key design principle:
  The state machine runs ONLY on confirmed daily closes — never on intraday or
  pre-market quotes. Live prices are shown as informational preview only.

Data sources:
  - Yahoo Finance: historical daily closes (long history for SMA + z-score seed)
  - Alpaca: live current price + confirmed previous close + market clock

Required Streamlit secrets:
    [alpaca]
    api_key = "PK..."
    api_secret = "..."
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# ============================================================================
# CONFIG
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

ET = ZoneInfo("America/New_York")
MARKET_OPEN_ET = time(9, 30)
MARKET_CLOSE_ET = time(16, 0)


# ============================================================================
# UTILITY
# ============================================================================
def esc(s: str) -> str:
    """Escape `$` so Streamlit markdown doesn't treat it as LaTeX."""
    return s.replace("$", r"\$")


def parse_iso(ts_str: str) -> datetime:
    """Parse ISO 8601 timestamp with Z → +00:00."""
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


# ============================================================================
# DATA FETCHING
# ============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_yahoo_history(ticker: str, period: str) -> pd.DataFrame:
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


@st.cache_data(ttl=60, show_spinner=False)
def fetch_alpaca_snapshot(ticker: str, _api_key: str, _api_secret: str, feed: str = "iex") -> dict:
    url = f"https://data.alpaca.markets/v2/stocks/snapshots?symbols={ticker}&feed={feed}"
    headers = {
        "APCA-API-KEY-ID": _api_key,
        "APCA-API-SECRET-KEY": _api_secret,
        "accept": "application/json",
    }
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    if ticker not in data:
        raise RuntimeError(f"Alpaca returned no snapshot for {ticker}")
    return data[ticker]


@st.cache_data(ttl=30, show_spinner=False)
def fetch_alpaca_clock(_api_key: str, _api_secret: str) -> dict:
    url = "https://paper-api.alpaca.markets/v2/clock"
    headers = {
        "APCA-API-KEY-ID": _api_key,
        "APCA-API-SECRET-KEY": _api_secret,
        "accept": "application/json",
    }
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def get_alpaca_credentials():
    try:
        return st.secrets["alpaca"]["api_key"], st.secrets["alpaca"]["api_secret"]
    except Exception:
        return None, None


# ============================================================================
# SESSION CLASSIFICATION
# ============================================================================
def determine_session_state(snap: dict, clock: dict) -> dict:
    """
    Classify the current market session. The state machine uses ONLY the
    'latest_complete_close' returned here — never an intraday or pre-market quote.
    """
    is_open = bool(clock.get("is_open", False))
    next_open = parse_iso(clock["next_open"]) if clock.get("next_open") else None
    next_close = parse_iso(clock["next_close"]) if clock.get("next_close") else None

    now_et = datetime.now(ET)
    today_et = now_et.date()

    daily_bar_date = parse_iso(snap["dailyBar"]["t"]).astimezone(ET).date()
    prev_daily_bar_date = parse_iso(snap["prevDailyBar"]["t"]).astimezone(ET).date()
    live_price = float(snap["latestTrade"]["p"])
    live_ts = parse_iso(snap["latestTrade"]["t"])

    if is_open:
        # Regular session in progress. Today's daily bar is intraday — use the previous bar.
        session = "OPEN"
        if daily_bar_date == today_et:
            latest_complete_close = float(snap["prevDailyBar"]["c"])
            latest_complete_date = prev_daily_bar_date
        else:
            latest_complete_close = float(snap["dailyBar"]["c"])
            latest_complete_date = daily_bar_date
    else:
        # Market closed (could be pre-market, post-market, weekend, holiday)
        if daily_bar_date == today_et and now_et.time() >= MARKET_CLOSE_ET:
            # Today's session is complete
            session = "POST_MARKET_FINAL"
            latest_complete_close = float(snap["dailyBar"]["c"])
            latest_complete_date = today_et
        elif next_open and next_open.astimezone(ET).date() == today_et:
            # Next open is today → we're in pre-market
            session = "PRE_MARKET"
            latest_complete_close = float(snap["dailyBar"]["c"])
            latest_complete_date = daily_bar_date
        else:
            # Weekend, holiday, or after-hours of a past session
            session = "CLOSED_OFF_DAY"
            latest_complete_close = float(snap["dailyBar"]["c"])
            latest_complete_date = daily_bar_date

    return {
        "session": session,
        "latest_complete_close": latest_complete_close,
        "latest_complete_date": latest_complete_date,
        "live_price": live_price,
        "live_ts": live_ts,
        "next_open": next_open,
        "next_close": next_close,
        "now_et": now_et,
        "is_open": is_open,
    }


# ============================================================================
# SERIES ASSEMBLY
# ============================================================================
def build_confirmed_series(yahoo_df: pd.DataFrame,
                            last_complete_close: float,
                            last_complete_date: date) -> pd.DataFrame:
    """Yahoo daily history with anything ≥ last_complete_date dropped,
    then Alpaca's confirmed close appended as authoritative."""
    df = yahoo_df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df[df["date"].dt.date < last_complete_date].copy()
    new_row = pd.DataFrame([{
        "date": pd.Timestamp(last_complete_date),
        "close": last_complete_close,
    }])
    df = pd.concat([df, new_row], ignore_index=True)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def build_preview_series(confirmed_df: pd.DataFrame,
                          live_price: float,
                          preview_date: date) -> pd.DataFrame:
    """Append a hypothetical bar with the live price for what-if computation."""
    df = confirmed_df.copy()
    last_date = df["date"].iloc[-1].date()
    if last_date >= preview_date:
        return df
    new_row = pd.DataFrame([{"date": pd.Timestamp(preview_date), "close": live_price}])
    return pd.concat([df, new_row], ignore_index=True)


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
# STATE MACHINE
# ============================================================================
def derive_lockout_state(d: pd.DataFrame) -> dict:
    valid = d.dropna(subset=["z_threshold", "rv20", "sma30", "sma30_lagged", "ext"]).copy()
    locked = False
    days_locked = 0
    last_trigger = {"date": None, "ext": None, "zthr": None}
    last_release = {"date": None, "reason": None}
    event_log = []

    for _, row in valid.iterrows():
        d_ = row["date"].date()
        ext = row["ext"]
        z_thresh = row["z_threshold"]
        sma30 = row["sma30"]
        sma30_lagged = row["sma30_lagged"]
        close = row["close"]
        if not locked:
            if ext >= z_thresh:
                locked = True
                days_locked = 0
                last_trigger = {"date": d_, "ext": ext, "zthr": z_thresh}
                event_log.append((d_, "FIRED", f"ext {ext:+.2f}% crossed z-threshold {z_thresh:+.2f}%"))
        else:
            days_locked += 1
            if ext <= 0:
                event_log.append((d_, "RELEASED (primary)", f"ext {ext:.2f}% ≤ 0 after {days_locked} days"))
                locked = False
                last_release = {"date": d_, "reason": "primary (ext ≤ 0)"}
                days_locked = 0
            elif (days_locked >= MIN_DAYS_LOCKED
                  and sma30 > sma30_lagged
                  and close > sma30
                  and ext < z_thresh):
                event_log.append((d_, "RELEASED (momentum)",
                                  f"all 4 conditions met after {days_locked} days"))
                locked = False
                last_release = {"date": d_, "reason": "momentum (D_rising_sma30)"}
                days_locked = 0
    return {
        "locked": locked,
        "days_locked": days_locked,
        "last_trigger": last_trigger,
        "last_release": last_release,
        "event_log": event_log,
    }


def make_decision(row, locked, days_locked):
    rv20 = row["rv20"]
    ext = row["ext"]
    sma30 = row["sma30"]
    sma30_lagged = row["sma30_lagged"]
    z_thresh = row["z_threshold"]
    close = row["close"]
    notes = []
    if not locked:
        if ext >= z_thresh:
            return True, 0, 0.0, [f"⚠️ Lockout fires: extension {ext:+.2f}% ≥ z-threshold {z_thresh:+.2f}%"]
        target = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
        return False, 0, target, [f"Engaged. Vol-target = min(50 / {rv20:.2f}, 1.0) = {target*100:.1f}%"]
    days_locked += 1
    notes.append(f"Day {days_locked} of lockout.")
    if ext <= 0:
        target = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
        notes.append(f"✅ PRIMARY RELEASE: ext {ext:.2f}% ≤ 0. Re-engage at {target*100:.1f}%.")
        return False, 0, target, notes
    if (days_locked >= MIN_DAYS_LOCKED and sma30 > sma30_lagged and close > sma30 and ext < z_thresh):
        target = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
        notes.append(f"✅ MOMENTUM RELEASE: all 4 conditions met. Re-engage at {target*100:.1f}%.")
        return False, 0, target, notes
    blocks = []
    if days_locked < MIN_DAYS_LOCKED:
        blocks.append(f"days_locked < {MIN_DAYS_LOCKED} (have {days_locked})")
    if sma30 <= sma30_lagged:
        blocks.append("SMA30 not rising")
    if close <= sma30:
        blocks.append("price not > SMA30")
    if ext >= z_thresh:
        blocks.append("ext not < threshold")
    notes.append("Still locked — release blocked by: " + "; ".join(blocks))
    return True, days_locked, 0.0, notes


# ============================================================================
# B2 + STANDARD NOISE BANDS — CONFIG
# ============================================================================
B2_SLOPE_LAG = 5               # EMA20/EMA50 compared with 5 sessions earlier
NOISE_LOOKBACK = 14            # prior valid sessions used for the noise profile
NOISE_CHECK_MINUTES = list(range(30, 361, 30))   # 10:00, 10:30 ... 15:30 ET
MIN_SESSION_BARS = 375         # a session needs >= 375 of 390 minute bars
MINUTE_HISTORY_DAYS = 35       # calendar days of 1-min bars to request


# ============================================================================
# B2 + NOISE BANDS — DATA
# ============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_yahoo_ohlc(ticker: str, period: str) -> pd.DataFrame:
    """Daily OHLC (split/dividend adjusted) for B2's EMAs and the daily replay."""
    df = yf.Ticker(ticker).history(period=period, auto_adjust=True, actions=False)
    if df is None or df.empty:
        raise RuntimeError(f"Yahoo returned no data for {ticker}")
    df = df.reset_index()
    df.columns = [str(c).strip().lower() for c in df.columns]
    date_col = next((c for c in df.columns if c in ("date", "datetime", "index")), None)
    out = df[[date_col, "open", "high", "low", "close"]].copy()
    out.columns = ["date", "open", "high", "low", "close"]
    out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None).dt.normalize()
    out = out.dropna().drop_duplicates(subset=["date"], keep="last")
    return out.sort_values("date").reset_index(drop=True)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_alpaca_minute_bars(ticker: str, start_iso: str, end_iso: str,
                             _api_key: str, _api_secret: str, feed: str = "iex") -> pd.DataFrame:
    """Regular-hours 1-minute bars, paginated. Minute index 0 = 9:30 ET."""
    url = f"https://data.alpaca.markets/v2/stocks/{ticker}/bars"
    headers = {"APCA-API-KEY-ID": _api_key, "APCA-API-SECRET-KEY": _api_secret,
               "accept": "application/json"}
    params = {"timeframe": "1Min", "start": start_iso, "end": end_iso, "limit": 10000,
              "adjustment": "split", "feed": feed, "sort": "asc"}
    rows = []
    while True:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        rows.extend(data.get("bars") or [])
        token = data.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    return bars_from_rows(rows)


def bars_from_rows(rows) -> pd.DataFrame:
    cols = ["ts", "session", "minute", "open", "high", "low", "close", "volume"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows).rename(columns={"t": "ts", "o": "open", "h": "high", "l": "low",
                                            "c": "close", "v": "volume"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(ET)
    df["session"] = df["ts"].dt.date
    df["minute"] = df["ts"].dt.hour * 60 + df["ts"].dt.minute - (9 * 60 + 30)
    df = df[(df["minute"] >= 0) & (df["minute"] < 390)]
    return df[cols].sort_values("ts").reset_index(drop=True)


def build_b2_daily(yahoo_ohlc: pd.DataFrame, snap: dict, last_complete_date: date) -> pd.DataFrame:
    """Yahoo OHLC through the day before the last complete session, plus Alpaca's
    confirmed bar for that session (same convention as the D-rising series)."""
    df = yahoo_ohlc[yahoo_ohlc["date"].dt.date < last_complete_date].copy()
    bar = None
    for key in ("dailyBar", "prevDailyBar"):
        b = snap.get(key)
        if b and parse_iso(b["t"]).astimezone(ET).date() == last_complete_date:
            bar = b
            break
    if bar is not None:
        df = pd.concat([df, pd.DataFrame([{
            "date": pd.Timestamp(last_complete_date), "open": float(bar["o"]),
            "high": float(bar["h"]), "low": float(bar["l"]), "close": float(bar["c"]),
        }])], ignore_index=True)
    df = df.sort_values("date").reset_index(drop=True)
    for n in (10, 20, 50):
        df[f"ema{n}"] = df["close"].ewm(span=n, adjust=False).mean()
    df["ema20_lag"] = df["ema20"].shift(B2_SLOPE_LAG)
    df["ema50_lag"] = df["ema50"].shift(B2_SLOPE_LAG)
    return df


# ============================================================================
# B2 — REGIME AND STATE MACHINE
# ============================================================================
def b2_regime(row) -> dict:
    """Regime for the NEXT session, from a completed day's EMAs."""
    e10, e20, e50 = row["ema10"], row["ema20"], row["ema50"]
    if e10 > e20 > e50 and e20 > row["ema20_lag"]:
        return {"name": "Strong bullish", "trail": 0.08, "rec": e10, "rec_name": "EMA10"}
    if e10 < e20 < e50 and e20 < row["ema20_lag"]:
        return {"name": "Bearish", "trail": 0.04, "rec": e20, "rec_name": "EMA20"}
    if row["ema50"] > row["ema50_lag"]:
        return {"name": "Transition (EMA50 rising)", "trail": 0.08, "rec": e20, "rec_name": "EMA20"}
    return {"name": "Transition", "trail": 0.06, "rec": e20, "rec_name": "EMA20"}


def b2_new_state() -> dict:
    return {"holding": False, "peak": 0.0, "armed": False, "count": 0,
            "pending": False, "entry_price": None, "entry_date": None, "events": []}


def b2_step_daily(st_: dict, day_row, reg: dict) -> None:
    """Daily-bar approximation (used only where minute bars are unavailable).
    Exit if the day's low reaches the threshold; re-enter at the close if the day
    traded at/below the recovery EMA (arming) and closed above it."""
    d_ = day_row["date"].date()
    rec, tr = reg["rec"], reg["trail"]
    if st_["holding"]:
        thr = min(st_["peak"] * (1 - tr), rec)
        if day_row["low"] <= thr:
            px = min(thr, day_row["open"])
            st_.update(holding=False, armed=day_row["close"] <= rec, count=0)
            st_["events"].append((d_, "EXIT (daily approx.)", px))
        else:
            st_["peak"] = max(st_["peak"], day_row["high"])
        return
    if day_row["low"] <= rec:
        st_["armed"] = True
    if st_["armed"] and day_row["close"] > rec:
        st_.update(holding=True, peak=day_row["close"], armed=False, count=0,
                   entry_price=day_row["close"], entry_date=d_)
        st_["events"].append((d_, "ENTRY (daily approx.)", day_row["close"]))


def b2_step_minutes(st_: dict, bars: pd.DataFrame, reg: dict, session_date) -> None:
    """Exact B2 logic on one session of 1-minute bars (the tested specification):
    stop = min(peak × (1 − trail), recovery EMA) against each bar's low; after an exit,
    a close at/below the recovery EMA arms re-entry; two consecutive closes above it
    schedule a buy at the next minute's open."""
    rec, tr = reg["rec"], reg["trail"]
    st_["count"] = 0
    for b in bars.itertuples(index=False):
        if st_["pending"]:
            st_.update(holding=True, peak=b.open, pending=False, armed=False, count=0,
                       entry_price=b.open, entry_date=session_date)
            st_["events"].append((session_date, f"ENTRY {b.ts.strftime('%H:%M')}", b.open))
            continue
        if st_["holding"]:
            thr = min(st_["peak"] * (1 - tr), rec)
            if b.low <= thr:
                px = min(thr, b.open)
                st_.update(holding=False, armed=b.close <= rec, count=0)
                st_["events"].append((session_date, f"EXIT {b.ts.strftime('%H:%M')}", px))
            else:
                st_["peak"] = max(st_["peak"], b.high)
            continue
        if b.close <= rec:
            st_["armed"] = True
            st_["count"] = 0
        elif st_["armed"]:
            st_["count"] += 1
            if st_["count"] >= 2:
                st_["pending"] = True
                st_["events"].append((session_date, f"RE-ENTRY SIGNAL {b.ts.strftime('%H:%M')}", b.close))


def b2_replay(daily: pd.DataFrame, minute_bars: pd.DataFrame, today_et: date,
              warmup: int = 60) -> dict:
    """Replay B2 through the last complete session (daily approximation for older
    history, exact minute logic for sessions that have 1-minute bars). Returns the
    state going into the next session, plus today's in-progress state if any."""
    st_ = b2_new_state()
    minute_sessions = set(minute_bars["session"].unique()) if len(minute_bars) else set()
    for i in range(max(warmup, B2_SLOPE_LAG + 1), len(daily)):
        reg = b2_regime(daily.iloc[i - 1])
        row = daily.iloc[i]
        d_ = row["date"].date()
        if d_ in minute_sessions:
            b2_step_minutes(st_, minute_bars[minute_bars["session"] == d_], reg, d_)
        else:
            b2_step_daily(st_, row, reg)
    st_["next_regime"] = b2_regime(daily.iloc[-1])
    st_["state_before_today"] = {k: st_[k] for k in ("holding", "peak", "armed", "pending")}
    today_bars = minute_bars[minute_bars["session"] == today_et] if len(minute_bars) else minute_bars
    if daily["date"].iloc[-1].date() < today_et and len(today_bars):
        b2_step_minutes(st_, today_bars, st_["next_regime"], today_et)
        st_["ran_today"] = True
    else:
        st_["ran_today"] = False
    return st_


# ============================================================================
# NOISE BANDS
# ============================================================================
def session_minutes(bars_one: pd.DataFrame) -> pd.DataFrame:
    """Reindex a session to minutes 0..389, forward-filling missing closes."""
    s = bars_one.set_index("minute").sort_index()
    s = s[~s.index.duplicated(keep="last")].reindex(range(390))
    s["close"] = s["close"].ffill()
    return s


def noise_profile(minute_bars: pd.DataFrame, before: date, lookback: int = NOISE_LOOKBACK):
    """Average |close/open − 1| at each minute over the last `lookback` valid sessions
    strictly before `before`."""
    valid = []
    for d_, g in minute_bars[minute_bars["session"] < before].groupby("session"):
        mins = set(g["minute"])
        if len(g) >= MIN_SESSION_BARS and 0 in mins and 389 in mins:
            valid.append(d_)
    used = valid[-lookback:]
    if not used:
        return None, []
    moves = []
    for d_ in used:
        s = session_minutes(minute_bars[minute_bars["session"] == d_])
        o = s["open"].iloc[0] if not np.isnan(s["open"].iloc[0]) else s["close"].dropna().iloc[0]
        moves.append(np.abs(s["close"].values / o - 1))
    return np.nanmean(np.vstack(moves), axis=0), used


def noise_today(today_bars: pd.DataFrame, noise: np.ndarray, prev_close: float) -> dict:
    """Bands, VWAP and the scheduled half-hour signals for the current session."""
    tb = today_bars.sort_values("minute")
    day_open = float(tb["open"].iloc[0])
    up_base, lo_base = max(day_open, prev_close), min(day_open, prev_close)
    tp = (tb["high"] + tb["low"] + tb["close"]) / 3
    vwap = (tp * tb["volume"]).cumsum() / tb["volume"].cumsum().replace(0, np.nan)
    tb = tb.assign(vwap=vwap.values)
    by_min = tb.set_index("minute")
    last_min = int(tb["minute"].iloc[-1])
    checks, position = [], 0
    for m in NOISE_CHECK_MINUTES:
        if last_min < m - 1:
            break
        prior = by_min.loc[:m - 1]
        if prior.empty:
            continue
        bar = prior.iloc[-1]
        pm = int(prior.index[-1])
        upper = up_base * (1 + noise[pm])
        lower = lo_base * (1 - noise[pm])
        c, vw = float(bar["close"]), float(bar["vwap"])
        sig = 1 if c > max(upper, vw) else (-1 if c < min(lower, vw) else 0)
        position = sig
        checks.append({"Check (ET)": f"{(570 + m) // 60}:{(570 + m) % 60:02d}",
                       "Close": c, "Upper band": upper, "Lower band": lower, "VWAP": vw,
                       "Signal": {1: "LONG TQQQ", -1: "SHORT (buy SQQQ)", 0: "FLAT"}[sig]})
    nxt = next((m for m in NOISE_CHECK_MINUTES if m - 1 > last_min), None)
    preview = None
    if nxt is not None:
        cur_vwap = float(tb["vwap"].iloc[-1])
        upper_n = up_base * (1 + noise[nxt - 1])
        lower_n = lo_base * (1 - noise[nxt - 1])
        preview = {"time": f"{(570 + nxt) // 60}:{(570 + nxt) % 60:02d}",
                   "long_above": max(upper_n, cur_vwap), "short_below": min(lower_n, cur_vwap),
                   "upper": upper_n, "lower": lower_n, "vwap": cur_vwap}
    return {"open": day_open, "prev_close": prev_close, "checks": checks, "position": position,
            "next": preview, "last_close": float(tb["close"].iloc[-1]), "vwap": float(tb["vwap"].iloc[-1])}


# ============================================================================
# B2 + NOISE BANDS — RENDERING
# ============================================================================
def render_b2_section(ticker, period, feed, api_key, api_secret, snap, session_state):
    st.header("🧭 B2 + Standard Noise Bands")
    st.caption(
        "B2 = daily EMA10/20/50 regime with an adaptive trailing stop and intraday re-entry. "
        "Noise bands trade only while B2 is out, on half-hour checks from 10:00 to 15:30 ET, "
        "and are always flat at the close. Short signals are executed by buying SQQQ."
    )
    SESSION_ = session_state["session"]
    last_complete_date = session_state["latest_complete_date"]
    now_et_ = session_state["now_et"]
    today_ = now_et_.date()

    try:
        with st.spinner("Loading daily OHLC and 1-minute bars for B2..."):
            ohlc = fetch_yahoo_ohlc(ticker, period)
            daily = build_b2_daily(ohlc, snap, last_complete_date)
            start = (now_et_ - timedelta(days=MINUTE_HISTORY_DAYS)).astimezone(ZoneInfo("UTC"))
            end = now_et_.astimezone(ZoneInfo("UTC"))
            if feed == "sip":   # free Alpaca plans can't query the most recent 15 minutes of SIP
                end = end - timedelta(minutes=16)
            bars = fetch_alpaca_minute_bars(ticker, start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                            end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                            api_key, api_secret, feed=feed)
    except Exception as e:
        st.error(f"B2 data unavailable: {e}")
        st.divider()
        return

    if len(daily) < 70:
        st.error("Not enough daily history for B2 (need about 70 sessions).")
        st.divider()
        return

    state_b2 = b2_replay(daily, bars, today_)
    reg = state_b2["next_regime"]

    with st.expander("⚙️ Override B2 state (if your account follows B2 and differs from the replay)"):
        st.caption("Older history is replayed on daily bars (approximation); the last ~5 weeks use exact "
                   "1-minute logic. If your actual B2 position differs, set it here.")
        if st.checkbox("Use manual B2 state", key="b2_override"):
            state_b2["holding"] = st.checkbox("B2 currently holding shares?", value=bool(state_b2["holding"]))
            state_b2["peak"] = st.number_input("Highest price since entry", min_value=0.0,
                                               value=float(state_b2["peak"] or 0.0), step=0.01)
            state_b2["armed"] = st.checkbox("Re-entry armed (traded at/below recovery EMA since exit)?",
                                            value=bool(state_b2["armed"]))

    live_px = session_state["live_price"]
    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("Regime (for today)" if SESSION_ in ("OPEN", "PRE_MARKET") else "Regime (next session)",
               reg["name"])
    rc2.metric("Trailing stop", f"{reg['trail'] * 100:.0f}%")
    rc3.metric(f"Recovery EMA ({reg['rec_name']})", f"${reg['rec']:.2f}",
               delta=f"{(live_px / reg['rec'] - 1) * 100:+.1f}% live vs EMA", delta_color="off")
    last = daily.iloc[-1]
    rc4.metric("EMA10 / 20 / 50", f"{last['ema10']:.2f} / {last['ema20']:.2f} / {last['ema50']:.2f}")

    if state_b2["holding"]:
        thr = min(state_b2["peak"] * (1 - reg["trail"]), reg["rec"])
        st.success(esc(
            f"🟢 **B2 IN — hold TQQQ shares.** Highest since entry ${state_b2['peak']:.2f}. "
            f"Exit if TQQQ trades at or below **${thr:.2f}** "
            f"(min of {reg['trail'] * 100:.0f}% trail ${state_b2['peak'] * (1 - reg['trail']):.2f} "
            f"and {reg['rec_name']} ${reg['rec']:.2f}). Noise bands inactive."
        ))
    else:
        if state_b2.get("pending"):
            st.success(esc(
                f"🟢 **B2 RE-ENTRY SIGNAL — buy TQQQ shares at the next minute's open.** "
                f"Close any noise-band position first."
            ))
        else:
            armed_txt = ("armed — needs **two consecutive 1-minute closes above** "
                         if state_b2["armed"] else
                         "not yet armed — price must first trade at/below, then make two 1-minute closes above ")
            st.error(esc(
                f"🔴 **B2 OUT — no TQQQ shares for B2.** Re-entry is {armed_txt}"
                f"**${reg['rec']:.2f}** ({reg['rec_name']}). Live ${live_px:.2f} is "
                f"{(reg['rec'] / live_px - 1) * 100:+.1f}% away. Noise bands are active."
            ))

    # --- Noise bands ---
    st.subheader("Noise bands (active only while B2 is out)")
    noise, used = noise_profile(bars, today_ if SESSION_ == "OPEN" else today_ + timedelta(days=1))
    if noise is None:
        st.warning("Not enough complete 1-minute sessions to build the noise profile. "
                   "Try the SIP feed; IEX bars can be too sparse.")
    else:
        if len(used) < NOISE_LOOKBACK:
            st.warning(f"Noise profile uses only {len(used)} valid sessions (target {NOISE_LOOKBACK}). "
                       "IEX bars may be missing minutes; the SIP feed is more reliable.")
        today_bars = bars[bars["session"] == today_] if len(bars) else bars
        if SESSION_ == "OPEN" and len(today_bars) and not state_b2["holding"]:
            nt = noise_today(today_bars, noise, session_state["latest_complete_close"])
            pos_txt = {1: "🟢 **LONG TQQQ** (100% of noise capital)",
                       -1: "🔴 **SHORT — hold SQQQ** (100% of noise capital)",
                       0: "⚪ **FLAT**"}[nt["position"]]
            if not nt["checks"]:
                pos_txt = "⚪ **FLAT** — first check is at 10:00 ET"
            st.markdown(esc(f"Current noise-band position: {pos_txt}. Close everything by the 4:00 PM close."))
            n1, n2, n3, n4 = st.columns(4)
            n1.metric("Today's open", f"${nt['open']:.2f}")
            n2.metric("Prior close", f"${nt['prev_close']:.2f}")
            n3.metric("VWAP now", f"${nt['vwap']:.2f}")
            n4.metric("Last price (bar)", f"${nt['last_close']:.2f}")
            if nt["next"]:
                nx = nt["next"]
                st.info(esc(
                    f"**Next check {nx['time']} ET** (signal from the 1-minute bar closing just before): "
                    f"LONG if close > **${nx['long_above']:.2f}** (upper band ${nx['upper']:.2f} and VWAP), "
                    f"SHORT if close < **${nx['short_below']:.2f}** (lower band ${nx['lower']:.2f} and VWAP), "
                    f"otherwise FLAT. VWAP moves during the half hour."
                ))
            if nt["checks"]:
                tbl = pd.DataFrame(nt["checks"])
                for col in ("Close", "Upper band", "Lower band", "VWAP"):
                    tbl[col] = tbl[col].map(lambda v: f"${v:.2f}")
                st.dataframe(tbl, use_container_width=True, hide_index=True)
        elif state_b2["holding"]:
            st.caption("B2 holds shares, so the noise-band system is off.")
        else:
            prof = pd.DataFrame({
                "Check (ET)": [f"{(570 + m) // 60}:{(570 + m) % 60:02d}" for m in NOISE_CHECK_MINUTES],
                "Expected move from open": [f"{noise[m - 1] * 100:.2f}%" for m in NOISE_CHECK_MINUTES],
            })
            st.caption(esc(
                "Market not open. Bands will be max(open, prior close) × (1 + move) and "
                "min(open, prior close) × (1 − move), combined with VWAP at each check. "
                f"Prior close ${session_state['latest_complete_close']:.2f}."
            ))
            st.dataframe(prof, use_container_width=True, hide_index=True)
        st.caption(f"Noise profile from {len(used)} sessions: "
                   f"{used[0] if used else '—'} to {used[-1] if used else '—'} · feed: {feed}.")

    with st.expander("Recent B2 events"):
        ev = state_b2["events"][-15:]
        if ev:
            st.dataframe(pd.DataFrame([(str(a), b, f"${c:.2f}") for a, b, c in ev],
                                      columns=["Date", "Event", "Price"]),
                         use_container_width=True, hide_index=True)
        else:
            st.write("No B2 events in the replay window.")
    st.divider()


# ============================================================================
# UI
# ============================================================================
st.set_page_config(page_title="TQQQ B2 + D_rising_sma30", page_icon="📈", layout="wide")
st.title("📈 TQQQ Daily Decision — B2 + Noise Bands and D_rising_sma30")
st.caption(
    "State machine runs only on confirmed closes. Live prices are preview only. "
    "Historical data: Yahoo Finance. Live price + clock: Alpaca."
)

# --- Sidebar ---
with st.sidebar:
    st.header("Settings")
    ticker = st.text_input("Ticker", value=DEFAULT_TICKER).strip().upper()
    period = st.selectbox("History lookback", ["5y", "10y", "max"], index=0)
    feed = st.selectbox("Alpaca feed", ["iex", "sip"], index=0)
    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    api_key, api_secret = get_alpaca_credentials()
    if api_key:
        st.success("✅ Alpaca credentials loaded")
    else:
        st.warning("⚠️ No Alpaca credentials.")
        with st.expander("How to add credentials"):
            st.markdown(
                "Add to Streamlit Cloud Secrets:\n```toml\n[alpaca]\napi_key = \"YOUR_KEY\"\napi_secret = \"YOUR_SECRET\"\n```"
            )

# --- Fetch ---
try:
    with st.spinner(f"Fetching {ticker} history from Yahoo..."):
        yahoo_df = fetch_yahoo_history(ticker, period)
except Exception as e:
    st.error(f"Failed to fetch Yahoo: {e}")
    st.stop()

if not (api_key and api_secret):
    st.error(
        "This app requires Alpaca credentials to determine the market session correctly. "
        "Add them to Streamlit Cloud secrets (see sidebar)."
    )
    st.stop()

try:
    with st.spinner(f"Fetching {ticker} live data from Alpaca..."):
        snap = fetch_alpaca_snapshot(ticker, api_key, api_secret, feed=feed)
        clock = fetch_alpaca_clock(api_key, api_secret)
except Exception as e:
    st.error(f"Failed to fetch Alpaca: {e}")
    st.stop()

session_state = determine_session_state(snap, clock)
SESSION = session_state["session"]
last_close = session_state["latest_complete_close"]
last_close_date = session_state["latest_complete_date"]
live_price = session_state["live_price"]
live_ts = session_state["live_ts"]
now_et = session_state["now_et"]
today_et = now_et.date()

# Build the confirmed series (state machine input)
confirmed_df = build_confirmed_series(yahoo_df, last_close, last_close_date)
confirmed_ind = compute_indicators(confirmed_df)
confirmed_last = confirmed_ind.iloc[-1]

if pd.isna(confirmed_last["ext_mean_504"]):
    st.error(
        f"Not enough history for 504-day rolling stats. "
        f"Need ~{SMA200_PERIOD + Z_LOOKBACK} bars; have {len(confirmed_ind)}."
    )
    st.stop()

# Auto-detected state from confirmed series
state = derive_lockout_state(confirmed_ind)

# Build preview series if we're OPEN
preview_ind = None
preview_state = None
preview_last = None
if SESSION == "OPEN":
    preview_df = build_preview_series(confirmed_df, live_price, today_et)
    preview_ind = compute_indicators(preview_df)
    preview_last = preview_ind.iloc[-1]
    preview_state = derive_lockout_state(preview_ind)


# ============================================================================
# RENDERING
# ============================================================================

# --- Session status banner ---
session_messages = {
    "PRE_MARKET": (
        "⏰ PRE-MARKET — today hasn't opened yet",
        "warning",
        "The strategy decision below is based on the LAST CONFIRMED CLOSE "
        "(yesterday or Friday). Today's eventual close will determine tomorrow's action. "
        "Don't trade on pre-market signals.",
    ),
    "OPEN": (
        "📊 MARKET OPEN — today is in progress",
        "warning",
        "The confirmed decision below uses yesterday's close. Today's eventual close at 4 PM ET "
        "will determine tomorrow's action. A live preview is shown further down — "
        "preview only, not the decision.",
    ),
    "POST_MARKET_FINAL": (
        "✅ TODAY'S CLOSE IS FINAL",
        "success",
        "The decision below is final — execute it at tomorrow's open.",
    ),
    "CLOSED_OFF_DAY": (
        "🔒 MARKET CLOSED (weekend / holiday / after-hours)",
        "info",
        "The decision below is final — execute it at the next trading day's open.",
    ),
}

label, banner_type, banner_text = session_messages[SESSION]
next_event_str = ""
if session_state["next_open"]:
    next_open_et = session_state["next_open"].astimezone(ET)
    next_event_str = f" Next open: {next_open_et.strftime('%a %b %d %I:%M %p ET')}."
if SESSION == "OPEN" and session_state["next_close"]:
    next_close_et = session_state["next_close"].astimezone(ET)
    next_event_str = f" Closes at {next_close_et.strftime('%I:%M %p ET')}."

full_banner = f"**{label}.** {banner_text}{next_event_str}"
if banner_type == "warning":
    st.warning(full_banner)
elif banner_type == "success":
    st.success(full_banner)
else:
    st.info(full_banner)

# --- Price block ---
st.header("💰 Prices")
pc1, pc2, pc3 = st.columns(3)
pc1.metric(
    "Last confirmed close",
    f"${last_close:.2f}",
    delta=f"{last_close_date.strftime('%a %b %d')}",
    delta_color="off",
    help="The most recent fully-completed daily close. This is what the strategy state machine uses.",
)
intraday_pct = (live_price / last_close - 1) * 100 if last_close else 0.0
pc2.metric(
    f"Live {ticker} price",
    f"${live_price:.2f}",
    delta=f"{intraday_pct:+.2f}% vs confirmed close",
    help="Latest trade from Alpaca. During pre-market or open hours, this is NOT today's close — "
         "it's a live snapshot that will move.",
)
pc3.metric(
    "Live price timestamp",
    live_ts.astimezone(ET).strftime("%I:%M %p ET"),
    delta=live_ts.astimezone(ET).strftime("%a %b %d"),
    delta_color="off",
)

st.divider()

# --- B2 + Standard Noise Bands (shown above the D-rising section) ---
render_b2_section(ticker, period, feed, api_key, api_secret, snap, session_state)

# --- Confirmed strategy state ---
st.header("🤖 Strategy state — based on confirmed closes")

if state["locked"]:
    tr = state["last_trigger"]
    st.error(
        f"🔒 **LOCKED** — day {state['days_locked']} of lockout. "
        f"Triggered on {tr['date']} (ext was {tr['ext']:+.2f}%, z-threshold {tr['zthr']:+.2f}%)."
    )
else:
    lr = state["last_release"]
    if lr["date"]:
        st.success(
            f"🟢 **ENGAGED** — no active lockout. "
            f"Last release: {lr['date']} via {lr['reason']}."
        )
    else:
        st.success("🟢 **ENGAGED** — no lockout has fired in this history window.")

st.caption(f"State machine evaluated through {last_close_date.strftime('%a %b %d')} close (${last_close:.2f}).")

with st.expander("Show all historical lockout events"):
    if state["event_log"]:
        events = pd.DataFrame(state["event_log"], columns=["Date", "Event", "Detail"])
        st.dataframe(events, use_container_width=True, hide_index=True)
    else:
        st.write("No lockout events in this history window.")

with st.expander("⚙️ Override auto-detected state (rare)"):
    st.caption("The state machine is authoritative. Override only if your broker account is intentionally out of sync.")
    override = st.checkbox("Use manual override")
    if override:
        manual_locked = st.checkbox("Manually set: currently locked?", value=state["locked"])
        manual_days = st.number_input(
            "Manual days locked", min_value=0, value=state["days_locked"], step=1,
            disabled=not manual_locked,
        )
        eff_locked = manual_locked
        eff_days = int(manual_days)
    else:
        eff_locked = state["locked"]
        eff_days = state["days_locked"]

st.divider()

# --- Narrative ---
st.header("💬 What the strategy is saying")

ext = confirmed_last["ext"]
z_thresh = confirmed_last["z_threshold"]
z_score = confirmed_last["z_score"]
rv20 = confirmed_last["rv20"]
close = confirmed_last["close"]
sma30 = confirmed_last["sma30"]
sma30_lagged = confirmed_last["sma30_lagged"]
sma200 = confirmed_last["sma200"]
sma30_rising = sma30 > sma30_lagged

trigger_price = sma200 * (1 + z_thresh / 100)
distance_to_trigger_pct = (trigger_price / close - 1) * 100

narrative = []
narrative.append(esc(
    f"At the {last_close_date.strftime('%a %b %d')} confirmed close of **${close:.2f}**, "
    f"TQQQ is **{ext:+.2f}%** above its 200-day average of **${sma200:.2f}**."
))

if eff_locked:
    tr = state["last_trigger"]
    narrative.append(esc(
        f"The lockout is **active** — fired on {tr['date']}, you're on day **{eff_days}**."
    ))
    release_ready = (eff_days >= MIN_DAYS_LOCKED and sma30_rising and close > sma30 and ext < z_thresh)
    if release_ready:
        narrative.append(esc(
            "✅ **All 4 release conditions are met at the confirmed close.** Release fires — "
            "buy at the next trading day's open."
        ))
    else:
        blockers = []
        if eff_days < MIN_DAYS_LOCKED:
            blockers.append(f"need ≥ {MIN_DAYS_LOCKED} days locked (have {eff_days})")
        if not sma30_rising:
            blockers.append("SMA30 isn't rising vs 5 days ago")
        if close <= sma30:
            blockers.append(f"close (${close:.2f}) isn't above SMA30 (${sma30:.2f})")
        if ext >= z_thresh:
            blockers.append(f"ext ({ext:+.2f}%) hasn't dropped below threshold — needs close below ${trigger_price:.2f}")
        narrative.append(esc(
            f"Release is **blocked** by: {'; '.join(blockers)}. Stay in cash next trading day."
        ))
else:
    if ext >= z_thresh:
        narrative.append(esc(
            f"⚠️ **Lockout fires** — extension ({ext:+.2f}%) crossed the z-threshold ({z_thresh:+.2f}%). "
            f"Sell to cash next trading day."
        ))
    else:
        target = min(VOL_TARGET / rv20, 1.0)
        narrative.append(esc(
            f"**No lockout** — extension is {z_thresh - ext:.2f} percentage points below the z-threshold "
            f"({z_thresh:+.2f}%). The lockout would fire if TQQQ closed at or above **${trigger_price:.2f}**."
        ))
        narrative.append(esc(
            f"With RV20 at {rv20:.2f}%, target weight = min(50 / {rv20:.2f}, 1.0) = **{target*100:.1f}%**."
        ))

for line in narrative:
    st.markdown(line)

st.divider()


# ============================================================================
# LIVE PREVIEW (only during OPEN session)
# ============================================================================
if SESSION == "OPEN" and preview_last is not None and preview_state is not None:
    st.header("🔮 Live preview — what would happen if today closed right now")
    st.caption(
        "This is NOT the decision. It shows what the strategy would do IF today closed at the current live price. "
        "Real close at 4 PM ET could differ."
    )

    # Show how confirmed state would change with this hypothetical close
    p_ext = preview_last["ext"]
    p_z_thresh = preview_last["z_threshold"]
    p_sma30 = preview_last["sma30"]
    p_sma30_lagged = preview_last["sma30_lagged"]
    p_sma30_rising = p_sma30 > p_sma30_lagged
    p_trigger_price = preview_last["sma200"] * (1 + p_z_thresh / 100)

    # Use the OVERRIDE state going into today (so we can preview "if today happens like this")
    p_new_locked, p_new_days, p_target, p_notes = make_decision(
        preview_last, locked=eff_locked, days_locked=eff_days
    )

    pv1, pv2, pv3 = st.columns(3)
    pv1.metric("Preview close", f"${live_price:.2f}", help="What the indicators below assume today closes at.")
    pv2.metric("Preview extension", f"{p_ext:+.2f}%")
    pv3.metric("Preview SMA30", f"${p_sma30:.2f}",
               delta="above" if live_price > p_sma30 else "below",
               delta_color="normal" if live_price > p_sma30 else "inverse")

    if eff_locked:
        # Was locked → would release if conditions met
        if not p_new_locked:
            st.success(
                f"PREVIEW: If today closes at ${live_price:.2f}, **release fires** → "
                f"buy at next-trading-day open at {p_target*100:.1f}% target."
            )
        else:
            blocks = []
            if eff_days + 1 < MIN_DAYS_LOCKED:
                blocks.append(f"days_locked = {eff_days + 1} (need ≥ {MIN_DAYS_LOCKED})")
            if not p_sma30_rising:
                blocks.append("SMA30 not rising")
            if live_price <= p_sma30:
                blocks.append(f"live price ${live_price:.2f} ≤ SMA30 ${p_sma30:.2f}")
            if p_ext >= p_z_thresh:
                blocks.append(f"ext {p_ext:+.2f}% ≥ threshold {p_z_thresh:+.2f}%")
            st.error(
                f"PREVIEW: If today closes at ${live_price:.2f}, **release stays blocked** by: {'; '.join(blocks)}. "
                f"Lockout continues."
            )

        # What price would trigger release today?
        # Release needs: live_price > p_sma30 (and ext < threshold)
        # p_sma30 is sensitive to today's close too, but minimally — approximate threshold = p_sma30
        breakeven_price = p_sma30
        st.info(esc(
            f"For the release to fire at today's close, TQQQ needs to close **above ~${breakeven_price:.2f}** "
            f"(approximate, since SMA30 moves slightly with today's close). "
            f"Current live price: ${live_price:.2f} → {(live_price - breakeven_price):+.2f} from that level."
        ))
    else:
        # Was engaged → would lockout fire?
        if p_new_locked:
            st.error(
                f"PREVIEW: If today closes at ${live_price:.2f}, **lockout fires** → "
                f"sell to cash at next-trading-day open."
            )
        else:
            st.success(
                f"PREVIEW: If today closes at ${live_price:.2f}, no lockout fires. Stay engaged at "
                f"{p_target*100:.1f}% target."
            )

    st.divider()


# --- Indicators (confirmed series) ---
st.header("📊 Indicators (at confirmed close)")

st.markdown("**Risk indicators (these decide whether the lockout fires)**")
r1c1, r1c2, r1c3, r1c4 = st.columns(4)
r1c1.metric("Extension", f"{ext:+.2f}%",
            help="(Close / SMA200 − 1) × 100. Lockout fires when this crosses the z-threshold.")
r1c2.metric("Z-threshold", f"{z_thresh:+.2f}%",
            delta=f"ext is {ext - z_thresh:+.2f} pp from trigger",
            delta_color="inverse" if ext < z_thresh else "normal",
            help="Rolling 2-year mean + 2 stdev of extension. Dynamic lockout level.")
r1c3.metric("Lockout fire price", f"${trigger_price:.2f}",
            delta=f"{distance_to_trigger_pct:+.1f}% from confirmed close",
            delta_color="off",
            help="Price level at which extension equals z-threshold. SMA200 × (1 + z_threshold/100).")
r1c4.metric("Z-score", f"{z_score:+.2f}σ",
            help="How many stdevs ext is from its 2-year mean.")

st.markdown("**Sizing**")
r2c1, r2c2 = st.columns(2)
r2c1.metric("RV20", f"{rv20:.2f}%",
            help="20-day annualized realized volatility. Drives sizing: min(50 / RV20, 1.0).")
target_now = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
r2c2.metric("Implied target weight (if engaged)", f"{target_now*100:.1f}%",
            help="What position size would be if not locked. Lockout overrides to 0%.")

st.markdown("**Re-entry signals (gate the momentum release)**")
r3c1, r3c2, r3c3, r3c4 = st.columns(4)
r3c1.metric("SMA30", f"${sma30:.2f}")
r3c2.metric("SMA30 5d ago", f"${sma30_lagged:.2f}",
            delta="rising" if sma30_rising else "falling",
            delta_color="normal" if sma30_rising else "inverse")
r3c3.metric("Close vs SMA30", "above" if close > sma30 else "below",
            delta=f"${close - sma30:+.2f}")
r3c4.metric("Days locked", str(eff_days) if eff_locked else "—")

st.markdown("**Context (informational only)**")
r4c1, r4c2 = st.columns(2)
r4c1.metric("SMA200", f"${sma200:.2f}")
r4c2.metric("60-day high", f"${confirmed_last['high60']:.2f}",
            delta="at new high" if confirmed_last["new_high"] else "below high",
            delta_color="off")

st.divider()

# --- Chart ---
with st.expander("📊 Extension vs Z-threshold (last 2 years)"):
    chart_df = confirmed_ind[["date", "ext", "z_threshold"]].dropna().tail(504).set_index("date")
    chart_df.columns = ["Extension %", "Z-threshold %"]
    st.line_chart(chart_df)

st.divider()

# --- Position ---
st.header("💼 Your position")
pc1, pc2 = st.columns(2)
nlv = pc1.number_input("Account NLV ($)", min_value=0.0, value=52000.0, step=1000.0, format="%.2f")
shares = pc2.number_input("Current TQQQ shares", min_value=0, value=0, step=1)

# --- Decision (based on confirmed state) ---
new_locked, new_days, target, notes = make_decision(confirmed_last, locked=eff_locked, days_locked=eff_days)
cur_value = shares * close
cur_weight = cur_value / nlv if nlv > 0 else 0.0
if abs(target - cur_weight) < REBALANCE_BAND:
    action_weight = cur_weight
    in_band = True
else:
    action_weight = target
    in_band = False
target_value = nlv * action_weight
target_shares = int(target_value / close) if close > 0 else 0
shares_to_trade = target_shares - int(shares)

st.divider()

# Frame the decision based on session
if SESSION == "POST_MARKET_FINAL":
    decision_header = f"🎯 Decision for tomorrow's open"
elif SESSION == "PRE_MARKET":
    decision_header = f"🎯 Decision for today's open (already determined by {last_close_date.strftime('%a %b %d')} close)"
elif SESSION == "OPEN":
    decision_header = f"🎯 Decision for today's open (already determined by {last_close_date.strftime('%a %b %d')} close)"
else:
    decision_header = f"🎯 Decision for next trading day's open"

st.header(decision_header)

if new_locked and shares == 0:
    st.error(f"🔒 **HOLD CASH** — lockout active, day {new_days}. No trade.")
elif new_locked and shares > 0:
    st.error(f"🔒 **SELL {abs(shares_to_trade)} shares at next open (MOO).** Lockout active.")
elif not new_locked and shares_to_trade > 0:
    st.success(f"🟢 **BUY {abs(shares_to_trade)} shares at next open (MOO).** Target {target*100:.1f}%.")
elif not new_locked and shares_to_trade < 0:
    st.warning(f"🟡 **SELL {abs(shares_to_trade)} shares** to rebalance down. Target {target*100:.1f}%.")
else:
    st.info(f"⚪ **HOLD** — no trade. Target {target*100:.1f}% ≈ current {cur_weight*100:.1f}%.")

dc1, dc2, dc3 = st.columns(3)
dc1.metric("Target weight", f"{action_weight*100:.1f}%",
           delta=f"{(action_weight - cur_weight)*100:+.1f} percentage points from current")
dc2.metric("Current weight", f"{cur_weight*100:.1f}%",
           delta=f"${cur_value:,.0f} position")
dc3.metric("State after decision",
           "LOCKED" if new_locked else "ENGAGED",
           delta=f"day {new_days}" if new_locked else None,
           delta_color="off")

st.markdown("**Reasoning:**")
for n in notes:
    st.markdown(esc(f"- {n}"))

if in_band and shares_to_trade == 0 and not new_locked:
    st.caption(f"💡 Within {REBALANCE_BAND*100:.0f}-pp rebalance band — no trade required.")

if action_weight > 0 and shares_to_trade != 0:
    stop_price = close * (1 - STOP_PCT)
    st.info(esc(
        f"🛡️ **Stop order**: After your buy fills, place a stop-MARKET sell at **${stop_price:.2f}** "
        f"(8% below confirmed close). Cancel at 3:55 PM ET — intraday only, not GTC."
    ))

st.divider()

# --- Tomorrow journal ---
with st.expander("📝 State to record for tomorrow's journal"):
    record = pd.DataFrame([{
        "Confirmed close date": str(last_close_date),
        "Confirmed close": f"${close:.2f}",
        "Session at runtime": SESSION,
        "Snapshot time": live_ts.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S ET"),
        "RV20 %": f"{rv20:.2f}",
        "Ext %": f"{ext:+.2f}",
        "Z-threshold %": f"{z_thresh:+.2f}",
        "Lockout fire price": f"${trigger_price:.2f}",
        "Z-score σ": f"{z_score:+.2f}",
        "Locked after decision": new_locked,
        "Days locked after": new_days,
        "Target weight %": f"{action_weight*100:.1f}",
        "Shares after fill": target_shares,
    }])
    st.dataframe(record.T.rename(columns={0: "value"}), use_container_width=True)

st.caption(
    f"Strategy: D_rising_sma30. State machine input: confirmed closes only. "
    f"Session: {SESSION}. Auto-state-machine traversed {len(state['event_log'])} historical events. "
    f"Yahoo cache: 60 min · Alpaca cache: 60 sec · Clock cache: 30 sec."
)
