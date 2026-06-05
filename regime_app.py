"""
TQQQ D_rising_sma30 Daily Decision Helper — Streamlit App
==========================================================

Data sources:
  - Yahoo Finance: historical daily closes (long history for SMA + z-score seed)
  - Alpaca: live current price + previous completed close (real-time accuracy)

Required Streamlit secrets (add to .streamlit/secrets.toml or Streamlit Cloud secrets UI):

    [alpaca]
    api_key = "PK..."
    api_secret = "..."

Without Alpaca credentials the app falls back to Yahoo for current price (less accurate intraday).
"""

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import requests
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
# UTILITY
# ============================================================================
def esc(s: str) -> str:
    """Escape `$` so Streamlit markdown doesn't treat it as LaTeX math."""
    return s.replace("$", r"\$")


# ============================================================================
# DATA FETCHING
# ============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_yahoo_history(ticker: str, period: str) -> pd.DataFrame:
    """Daily adjusted closes from Yahoo. Cached for 1 hour."""
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
    """Latest trade + previous daily bar from Alpaca. Cached for 60 seconds."""
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
    """US market clock from Alpaca. Cached for 30 seconds."""
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
    """Try to load Alpaca credentials from Streamlit secrets. Returns (key, secret) or (None, None)."""
    try:
        return st.secrets["alpaca"]["api_key"], st.secrets["alpaca"]["api_secret"]
    except Exception:
        return None, None


# ============================================================================
# INDICATORS
# ============================================================================
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
# LOCKOUT STATE MACHINE
# ============================================================================
def derive_lockout_state(d: pd.DataFrame):
    valid = d.dropna(subset=["z_threshold", "rv20", "sma30", "sma30_lagged", "ext"]).copy()
    locked = False
    days_locked = 0
    last_trigger_date = None
    last_trigger_ext = None
    last_trigger_zthr = None
    last_release_date = None
    last_release_reason = None
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
                last_trigger_date = d_
                last_trigger_ext = ext
                last_trigger_zthr = z_thresh
                event_log.append((d_, "FIRED", f"ext {ext:+.2f}% crossed z-threshold {z_thresh:+.2f}%"))
        else:
            days_locked += 1
            if ext <= 0:
                event_log.append((d_, "RELEASED (primary)", f"ext {ext:.2f}% fell to/below 0 after {days_locked} days"))
                locked = False
                last_release_date = d_
                last_release_reason = "primary (ext ≤ 0)"
                days_locked = 0
            elif (days_locked >= MIN_DAYS_LOCKED and sma30 > sma30_lagged and close > sma30 and ext < z_thresh):
                event_log.append((d_, "RELEASED (momentum)", f"SMA30 rising + price>SMA30 + ext<threshold after {days_locked} days"))
                locked = False
                last_release_date = d_
                last_release_reason = "momentum (D_rising_sma30)"
                days_locked = 0
    return {
        "locked": locked,
        "days_locked": days_locked,
        "last_trigger_date": last_trigger_date,
        "last_trigger_ext": last_trigger_ext,
        "last_trigger_zthr": last_trigger_zthr,
        "last_release_date": last_release_date,
        "last_release_reason": last_release_reason,
        "event_log": event_log,
    }


# ============================================================================
# DECISION
# ============================================================================
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
# UI
# ============================================================================
st.set_page_config(page_title="TQQQ D_rising_sma30", page_icon="📈", layout="wide")
st.title("📈 TQQQ D_rising_sma30 Daily Decision")
st.caption(
    "Volatility-targeted long TQQQ with z-score lockout, SMA30 momentum re-entry, "
    "and 8% same-day crash stop. Historical data: Yahoo Finance. Live price: Alpaca."
)

# --- Sidebar ---
with st.sidebar:
    st.header("Settings")
    ticker = st.text_input("Ticker", value=DEFAULT_TICKER).strip().upper()
    period = st.selectbox("History lookback", ["5y", "10y", "max"], index=0)
    feed = st.selectbox("Alpaca feed", ["iex", "sip"], index=0,
                        help="IEX is free; SIP requires a paid Alpaca subscription.")
    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    api_key, api_secret = get_alpaca_credentials()
    if api_key:
        st.success("✅ Alpaca credentials loaded")
    else:
        st.warning("⚠️ No Alpaca credentials — falling back to Yahoo for live price.")
        with st.expander("How to add Alpaca credentials"):
            st.markdown(
                "Add these to your Streamlit Cloud app's **Secrets** page "
                "(Settings → Secrets), or to `.streamlit/secrets.toml` locally:\n\n"
                "```toml\n"
                "[alpaca]\n"
                "api_key = \"YOUR_KEY\"\n"
                "api_secret = \"YOUR_SECRET\"\n"
                "```"
            )

# --- Fetch Yahoo history ---
try:
    with st.spinner(f"Fetching {ticker} history from Yahoo..."):
        df = fetch_yahoo_history(ticker, period)
except Exception as e:
    st.error(f"Failed to fetch Yahoo history for {ticker}: {e}")
    st.stop()

# Drop today's bar from Yahoo (it may be intraday/incomplete)
today = date.today()
df = df[df["date"].dt.date < today].reset_index(drop=True)

# --- Fetch Alpaca live data ---
alpaca_snap = None
live_price = None
live_ts = None
prev_close = None
prev_close_date = None
data_source_note = ""

if api_key and api_secret:
    try:
        with st.spinner(f"Fetching {ticker} live price from Alpaca..."):
            alpaca_snap = fetch_alpaca_snapshot(ticker, api_key, api_secret, feed=feed)
        live_price = alpaca_snap["latestTrade"]["p"]
        live_ts_raw = alpaca_snap["latestTrade"]["t"]
        # Parse Alpaca's RFC 3339 timestamp
        live_ts = datetime.fromisoformat(live_ts_raw.replace("Z", "+00:00"))
        prev_close = alpaca_snap["prevDailyBar"]["c"]
        prev_close_date = datetime.fromisoformat(
            alpaca_snap["prevDailyBar"]["t"].replace("Z", "+00:00")
        ).date()
        data_source_note = f"Live price from Alpaca ({feed.upper()} feed) at {live_ts.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}."
    except Exception as e:
        st.warning(f"Alpaca fetch failed: {e}. Using Yahoo's last close as current price.")
        alpaca_snap = None

if alpaca_snap is None:
    # Fall back: use Yahoo's last bar as both prev close and current
    live_price = float(df["close"].iloc[-1])
    live_ts = None
    prev_close = float(df["close"].iloc[-1])
    prev_close_date = df["date"].iloc[-1].date()
    data_source_note = "Current price = Yahoo's last close. No live Alpaca data available."

# Append today's "close" using the live price for indicator computation
today_row = pd.DataFrame([{"date": pd.Timestamp(today), "close": live_price}])
combined = pd.concat([df, today_row], ignore_index=True)

# --- Compute indicators ---
d = compute_indicators(combined)
last = d.iloc[-1]

if pd.isna(last["ext_mean_504"]):
    st.error(
        f"Not enough history for 504-day rolling stats. "
        f"Need ~{SMA200_PERIOD + Z_LOOKBACK} bars; have {len(d)}. Try a longer lookback."
    )
    st.stop()

# --- TOP: Market status + Live price banner ---
st.header("💰 Current price")

# Market clock — strong intraday warning
market_is_open = None
market_clock_note = ""
if api_key and api_secret:
    try:
        clock = fetch_alpaca_clock(api_key, api_secret)
        market_is_open = bool(clock.get("is_open"))
        next_event_iso = clock.get("next_close") if market_is_open else clock.get("next_open")
        if next_event_iso:
            next_event = datetime.fromisoformat(next_event_iso.replace("Z", "+00:00"))
            label = "closes" if market_is_open else "opens"
            market_clock_note = f"Market {label} at {next_event.astimezone().strftime('%H:%M %Z on %a %b %d')}."
    except Exception:
        pass

if market_is_open is True:
    st.warning(
        "⏰ **Market is OPEN — this is a preliminary preview, not the final decision.** "
        "Indicators below assume the live price IS today's close. The actual close may differ "
        f"as TQQQ moves over the rest of the session. {market_clock_note} "
        "Re-run after 4:00 PM ET for the official action."
    )
elif market_is_open is False:
    st.success(f"🔒 **Market is CLOSED — today's close is final.** {market_clock_note}")

intraday_change_pct = (live_price / prev_close - 1) * 100 if prev_close else 0.0

pc1, pc2, pc3 = st.columns(3)
pc1.metric(
    f"Live {ticker} price",
    f"${live_price:.2f}",
    delta=f"{intraday_change_pct:+.2f}% from prev close",
    help="Latest trade price from Alpaca (IEX feed by default). Updates every 60 seconds.",
)
pc2.metric(
    "Previous completed close",
    f"${prev_close:.2f}",
    delta=str(prev_close_date),
    delta_color="off",
    help="The last fully-completed daily close. Indicators below treat the live price as if today closed at it.",
)
pc3.metric(
    "Intraday change today",
    f"{intraday_change_pct:+.2f}%",
    delta=f"${live_price - prev_close:+.2f}",
    delta_color="off",
    help="Change from previous completed close to current live price.",
)

st.caption(data_source_note + " Strategy fires at the 4 PM ET close — final values may differ from the intraday snapshot above.")

st.divider()

# --- Auto-detected lockout state ---
state = derive_lockout_state(d)
st.header("🤖 Auto-detected strategy state")
if state["locked"]:
    st.error(
        f"🔒 **LOCKED** — day {state['days_locked']} of lockout. "
        f"Triggered on {state['last_trigger_date']} "
        f"(ext was {state['last_trigger_ext']:+.2f}%, z-threshold {state['last_trigger_zthr']:+.2f}%)."
    )
else:
    if state["last_release_date"]:
        st.success(
            f"🟢 **ENGAGED** — no active lockout. "
            f"Last release: {state['last_release_date']} via {state['last_release_reason']}."
        )
    else:
        st.success("🟢 **ENGAGED** — no lockout has fired in this history window.")

with st.expander("Show all historical lockout events"):
    if state["event_log"]:
        events = pd.DataFrame(state["event_log"], columns=["Date", "Event", "Detail"])
        st.dataframe(events, use_container_width=True, hide_index=True)
    else:
        st.write("No lockout events in this history window.")

with st.expander("⚙️ Override auto-detected state (rare — only if you disagree)"):
    st.caption("The state machine walks every day of history and is authoritative. Override only if your broker account is intentionally out of sync.")
    override = st.checkbox("Use manual override instead of auto-detected state")
    if override:
        manual_locked = st.checkbox("Manually set: currently locked?", value=state["locked"])
        manual_days = st.number_input("Manual days locked", min_value=0, value=state["days_locked"], step=1, disabled=not manual_locked)
        eff_locked = manual_locked
        eff_days = int(manual_days)
    else:
        eff_locked = state["locked"]
        eff_days = state["days_locked"]

st.divider()

# --- Narrative ---
st.header("💬 What's happening today")

ext = last["ext"]
z_thresh = last["z_threshold"]
z_score = last["z_score"]
rv20 = last["rv20"]
close = last["close"]
sma30 = last["sma30"]
sma200 = last["sma200"]
sma30_rising = last["sma30"] > last["sma30_lagged"]

trigger_price = sma200 * (1 + z_thresh / 100)
distance_to_threshold = z_thresh - ext
distance_to_trigger_pct = (trigger_price / close - 1) * 100

narrative_lines = []
narrative_lines.append(esc(
    f"At current live price **${close:.2f}**, TQQQ is **{ext:+.2f}%** above its 200-day average of **${sma200:.2f}**."
))

if eff_locked:
    narrative_lines.append(esc(
        f"The lockout is **active** — it fired on {state['last_trigger_date']} "
        f"when extension crossed the z-threshold. You're on day **{eff_days}** of lockout."
    ))
    release_ready = (eff_days >= MIN_DAYS_LOCKED and sma30_rising and close > sma30 and ext < z_thresh)
    if release_ready:
        narrative_lines.append(esc(
            "✅ **All 4 release conditions are met.** The lockout releases — buy back in at vol-target weight tomorrow at the open."
        ))
    else:
        blockers = []
        if eff_days < MIN_DAYS_LOCKED:
            blockers.append(f"need at least {MIN_DAYS_LOCKED} days locked (have {eff_days})")
        if not sma30_rising:
            blockers.append("SMA30 isn't rising vs 5 days ago")
        if close <= sma30:
            blockers.append(f"price (${close:.2f}) isn't above SMA30 (${sma30:.2f})")
        if ext >= z_thresh:
            blockers.append(f"extension ({ext:+.2f}%) hasn't dropped below threshold — need TQQQ to close below ${trigger_price:.2f}")
        narrative_lines.append(esc(
            f"Release is **blocked** because: {'; '.join(blockers)}. Stay in cash tomorrow."
        ))
else:
    if ext >= z_thresh:
        narrative_lines.append(esc(
            f"⚠️ **Lockout fires** — extension ({ext:+.2f}%) crossed the z-threshold ({z_thresh:+.2f}%). Sell to cash tomorrow at the open."
        ))
    else:
        target = min(VOL_TARGET / rv20, 1.0)
        narrative_lines.append(esc(
            f"**No lockout** — extension ({ext:+.2f}%) is **{distance_to_threshold:.2f} percentage points below** "
            f"the z-threshold ({z_thresh:+.2f}%). The lockout would fire if TQQQ closed at or above **${trigger_price:.2f}** "
            f"(that's {distance_to_trigger_pct:+.1f}% from current price)."
        ))
        narrative_lines.append(esc(
            f"With RV20 at {rv20:.2f}%, target weight = min(50 / {rv20:.2f}, 1.0) = **{target*100:.1f}%**."
        ))

for line in narrative_lines:
    st.markdown(line)

# --- Knife-catching diagnostics ---
release_ready_now = (eff_locked and eff_days >= MIN_DAYS_LOCKED and sma30_rising
                     and close > sma30 and ext < z_thresh)

if release_ready_now:
    st.divider()
    st.subheader("🔪 Knife-catching diagnostics")
    st.markdown(esc(
        "The release fires, but a release isn't proof the bottom is in. "
        "Here's the context for evaluating knife risk:"
    ))

    # Recent peak context
    recent_window = d.tail(60)
    recent_peak = recent_window["close"].max()
    recent_peak_date = recent_window.loc[recent_window["close"].idxmax(), "date"].date()
    drawdown_from_peak = (close / recent_peak - 1) * 100
    days_since_peak = (last["date"].date() - recent_peak_date).days

    # Vulnerability prices — what close levels would break each release condition?
    # Condition 3: price > SMA30  ⇒ vulnerable below SMA30
    price_vuln = sma30
    # Condition 4: ext < z_threshold  ⇒ vulnerable above trigger_price (which is already shown)

    kc1, kc2, kc3 = st.columns(3)
    kc1.metric(
        "Drawdown from 60d peak",
        f"{drawdown_from_peak:+.2f}%",
        delta=f"peak ${recent_peak:.2f} on {recent_peak_date}",
        delta_color="off",
        help="How far the current price has fallen from the 60-day high. "
             "A large drawdown right before a release signal often means the pullback isn't done.",
    )
    kc2.metric(
        "Days since 60d peak",
        str(days_since_peak),
        help="How long ago the 60-day peak was set. Releases within a few days of a fresh peak are higher-risk.",
    )
    kc3.metric(
        "Release-blocking price",
        f"${price_vuln:.2f}",
        delta=f"{(price_vuln/close - 1)*100:+.1f}% from current",
        delta_color="off",
        help="If today's close falls below this level (today's SMA30), the 'price > SMA30' release condition "
             "breaks and the lockout stays active. Watch this if running intraday.",
    )

    st.info(esc(
        f"⚠️ **Watch the SMA30 line at \\${price_vuln:.2f}**. "
        f"If TQQQ closes below this today, the release will NOT fire and you stay in cash. "
        f"If it closes above, the release fires and you buy at Monday's open. "
        f"Historical context: D_rising_sma30 has caught knives ~23% of the time. "
        f"The 8% intraday stop is the only protection against the bad releases. "
        f"The 2020-02-24 release fired with all 4 conditions met and was followed by a -36% TQQQ drawdown over 10 days."
    ))

st.divider()

# --- Indicators ---
st.header("📊 Indicators — what each one means")

st.markdown("**Risk indicators (these decide whether the lockout fires)**")
r1c1, r1c2, r1c3, r1c4 = st.columns(4)
r1c1.metric(
    "Extension",
    f"{ext:+.2f}%",
    help="How far TQQQ's price is above (or below) its 200-day moving average. Formula: (Close / SMA200 − 1) × 100. The headline risk gauge — lockout fires when extension crosses the z-threshold.",
)
r1c2.metric(
    "Z-threshold (%)",
    f"{z_thresh:+.2f}%",
    delta=f"ext is {ext - z_thresh:+.2f} percentage points from trigger",
    delta_color="inverse" if ext < z_thresh else "normal",
    help="The dynamic lockout trigger expressed as a percentage extension. Formula: rolling 2-year mean of extension + 2 × rolling 2-year stdev of extension. When today's extension reaches this level, the lockout fires.",
)
r1c3.metric(
    "Lockout fire price",
    f"${trigger_price:.2f}",
    delta=f"{distance_to_trigger_pct:+.1f}% from current price",
    delta_color="off",
    help="The actual TQQQ price level at which the lockout fires. Formula: SMA200 × (1 + z_threshold/100). If TQQQ closes at or above this price, lockout activates and we go to cash. If already locked, this is also the price below which the 'ext < threshold' release condition is satisfied.",
)
r1c4.metric(
    "Z-score",
    f"{z_score:+.2f}σ",
    help="How many standard deviations today's extension is from its 2-year mean. Z ≥ +2σ = lockout territory. Z ≈ 0 = at 2-year average. Z ≤ −2σ = extremely depressed.",
)

st.markdown("**Sizing indicator (this decides how big the position is)**")
r2c1, r2c2 = st.columns(2)
r2c1.metric(
    "RV20",
    f"{rv20:.2f}%",
    help="20-day annualized realized volatility of TQQQ. Formula: stdev of last 20 daily returns × √252 × 100. Drives position sizing: target_weight = min(50 / RV20, 1.0). When RV20 = 50%, target = 100%. When RV20 = 100%, target = 50%. Cap is 100% — no extra leverage on top of TQQQ.",
)
target_now = min(VOL_TARGET / rv20, 1.0) if rv20 > 0 else 0.0
r2c2.metric(
    "Implied target weight (if not locked)",
    f"{target_now*100:.1f}%",
    help="What your position size would be RIGHT NOW if the lockout weren't active. Computed as min(50 / RV20, 1.0). When lockout is on, actual target = 0%.",
)

st.markdown("**Re-entry signals (these gate the momentum release out of lockout)**")
r3c1, r3c2, r3c3, r3c4 = st.columns(4)
r3c1.metric(
    "SMA30",
    f"${sma30:.2f}",
    help="30-day simple moving average. The momentum release requires SMA30 to be rising AND price to be above it.",
)
r3c2.metric(
    "SMA30 5d ago",
    f"${last['sma30_lagged']:.2f}",
    delta="rising" if sma30_rising else "falling",
    delta_color="normal" if sma30_rising else "inverse",
    help="SMA30 from 5 trading days ago. Momentum release requires today's SMA30 > the SMA30 from 5 days ago.",
)
r3c3.metric(
    "Price vs SMA30",
    "above" if close > sma30 else "below",
    delta=f"${close - sma30:+.2f}",
    help="Required for momentum release: today's close must be above today's SMA30. If price is below SMA30, the trend hasn't reasserted itself.",
)
r3c4.metric(
    "Days locked",
    str(eff_days) if eff_locked else "—",
    help="How many trading days the lockout has been active. Momentum release requires days_locked ≥ 5 (prevents whipsaw re-entry).",
)

st.markdown("**Context (informational only — these don't trigger anything)**")
r4c1, r4c2, r4c3 = st.columns(3)
r4c1.metric(
    "Live close (used for indicators)",
    f"${close:.2f}",
    help="The price used by all indicators above. Either today's Alpaca live price (intraday) or Yahoo's last close (post-market).",
)
r4c2.metric(
    "SMA200",
    f"${sma200:.2f}",
    help="200-day simple moving average. The baseline that extension is measured against.",
)
r4c3.metric(
    "60-day high",
    f"${last['high60']:.2f}",
    delta="at new high" if last["new_high"] else "below high",
    delta_color="off",
    help="Highest close in the last 60 trading days. Diagnostic only — not used by this strategy.",
)

st.divider()

# --- Chart ---
with st.expander("📊 Chart: Extension vs Z-threshold over the last 2 years", expanded=False):
    chart_df = d[["date", "ext", "z_threshold"]].dropna().tail(504).set_index("date")
    chart_df.columns = ["Extension %", "Z-threshold %"]
    st.line_chart(chart_df)
    st.caption("When the blue line (extension) crosses ABOVE the orange line (z-threshold), the lockout fires.")

st.divider()

# --- Position ---
st.header("💼 Your position")
pc1, pc2 = st.columns(2)
nlv = pc1.number_input("Account NLV ($)", min_value=0.0, value=100000.0, step=1000.0, format="%.2f")
shares = pc2.number_input("Current TQQQ shares", min_value=0, value=0, step=1)

# --- Decision ---
new_locked, new_days, target, notes = make_decision(last, locked=eff_locked, days_locked=eff_days)
cur_value = shares * close
cur_weight = cur_value / nlv if nlv > 0 else 0.0
if abs(target - cur_weight) < REBALANCE_BAND:
    action_weight = cur_weight
    in_band = True
else:
    action_weight = target
    in_band = False
target_value = nlv * action_weight
target_shares = int(target_value / close)
shares_to_trade = target_shares - int(shares)

st.divider()
st.header("🎯 Decision for next trading day")

if new_locked and shares == 0:
    st.error(f"🔒 **HOLD CASH** — lockout active, day {new_days}. No trade.")
elif new_locked and shares > 0:
    st.error(f"🔒 **SELL {abs(shares_to_trade)} shares at next open (MOO).** Lockout active — go to cash.")
elif not new_locked and shares_to_trade > 0:
    st.success(f"🟢 **BUY {abs(shares_to_trade)} shares at next open (MOO).** Target weight {target*100:.1f}%.")
elif not new_locked and shares_to_trade < 0:
    st.warning(f"🟡 **SELL {abs(shares_to_trade)} shares** to rebalance down. Target weight {target*100:.1f}%.")
else:
    st.info(f"⚪ **HOLD** — no trade needed. Target {target*100:.1f}% ≈ current {cur_weight*100:.1f}%.")

dc1, dc2, dc3 = st.columns(3)
dc1.metric("Target weight", f"{action_weight*100:.1f}%",
           delta=f"{(action_weight - cur_weight)*100:+.1f} percentage points from current")
dc2.metric("Current weight", f"{cur_weight*100:.1f}%",
           delta=f"${cur_value:,.0f} position")
dc3.metric("State after today",
           "LOCKED" if new_locked else "ENGAGED",
           delta=f"day {new_days}" if new_locked else None,
           delta_color="off")

st.markdown("**Reasoning:**")
for n in notes:
    st.markdown(esc(f"- {n}"))

if in_band and shares_to_trade == 0 and not new_locked:
    st.caption(f"💡 Within {REBALANCE_BAND*100:.0f} percentage point rebalance band — no trade required.")

if action_weight > 0:
    stop_price = close * (1 - STOP_PCT)
    st.info(esc(
        f"🛡️ **Stop order**: After your buy fills, place a stop-MARKET sell at **${stop_price:.2f}** "
        f"(8% below current price). Cancel at 3:55 PM ET — intraday only, not GTC."
    ))

st.divider()

# --- Tomorrow record ---
with st.expander("📝 State to record for tomorrow's journal"):
    record = pd.DataFrame([{
        "Snapshot time": live_ts.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if live_ts else str(today),
        "Live price": f"${close:.2f}",
        "Previous close": f"${prev_close:.2f}",
        "RV20 %": f"{rv20:.2f}",
        "Ext %": f"{ext:+.2f}",
        "Z-threshold %": f"{z_thresh:+.2f}",
        "Lockout fire price": f"${trigger_price:.2f}",
        "Z-score σ": f"{z_score:+.2f}",
        "Locked tomorrow": new_locked,
        "Days locked tomorrow": new_days,
        "Target weight %": f"{action_weight*100:.1f}",
        "Shares after fill": target_shares,
    }])
    st.dataframe(record.T.rename(columns={0: "value"}), use_container_width=True)

st.caption(
    f"Strategy: D_rising_sma30. {data_source_note} "
    f"Auto-state-machine applied to {len(state['event_log'])} historical lockout events. "
    f"Yahoo cache: 60 min. Alpaca cache: 60 sec. Use ⟳ Refresh in the sidebar to force-reload."
)
