import streamlit as st
import statistics
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Page config

st.set_page_config(
page_title=“V10 Regime Check”,
page_icon=“📊”,
layout=“centered”,
initial_sidebar_state=“collapsed”,
)

# Styling

st.markdown(”””

<style>
    .stApp { background-color: #0a0a0f; }
    .block-container { padding: 1rem 1rem 2rem; max-width: 480px; }
    h1 { color: #f1f5f9 !important; font-size: 1.4rem !important; }
    h2 { font-size: 1rem !important; color: #94a3b8 !important;
         letter-spacing: 0.1em; text-transform: uppercase; }
    p, li { color: #cbd5e1; font-size: 0.9rem; }
    .regime-card {
        border-radius: 14px; padding: 18px; margin-bottom: 12px;
        border: 2px solid; font-family: monospace;
    }
    .metric-card {
        background: #0f172a; border: 1px solid #1e293b;
        border-radius: 10px; padding: 12px; margin-bottom: 8px;
    }
    .metric-label { color: #64748b; font-size: 0.75rem;
                    letter-spacing: 0.1em; text-transform: uppercase; }
    .metric-value { color: #f1f5f9; font-size: 1.2rem;
                    font-weight: 700; font-family: monospace; }
    .check-item {
        background: #0f172a; border: 1px solid #1e293b;
        border-radius: 8px; padding: 10px 12px; margin-bottom: 6px;
        color: #94a3b8; font-size: 0.85rem;
    }
    .stButton > button {
        width: 100%;
        background: linear-gradient(135deg, #2563eb, #4f46e5);
        color: white; border: none; border-radius: 12px;
        padding: 14px; font-size: 1rem; font-weight: 700;
        cursor: pointer; margin-bottom: 8px;
    }
</style>

“””, unsafe_allow_html=True)

# V10 Calculations

def calc_sma(prices, period):
if len(prices) < period:
return None
return sum(prices[-period:]) / period

def calc_rsi(prices, period=14):
if len(prices) < period + 1:
return None
gains, losses = [], []
for i in range(len(prices) - period, len(prices)):
d = prices[i] - prices[i-1]
gains.append(max(d, 0))
losses.append(max(-d, 0))
ag = sum(gains) / period
al = sum(losses) / period
if al == 0:
return 100.0
return round(100 - 100 / (1 + ag/al), 1)

def determine_regime(qqq, sma200, vix):
above = qqq > sma200
pct = (qqq - sma200) / sma200 * 100
if vix > 40:
return “CAPITULATION”, pct
if not above:
pct_below = (sma200 - qqq) / sma200 * 100
if pct_below <= 5 and 20 <= vix <= 28:
return “NO MAN’S LAND”, pct
return “STRESS”, pct
if vix < 20:
return “TREND”, pct
return “TENSION”, pct

REGIMES = {
“TREND”: {
“color”: “#16a34a”, “bg”: “#052e16”, “border”: “#166534”,
“icon”: “UP”,
“contracts”: “6 contracts”, “dte”: “7 DTE”, “delta”: “0.20-0.30”,
“instrument”: “TQQQ cash-secured puts”,
“action”: “Full CSP engine active. Set GTC profit-take at 50% immediately after fill.”,
“checks”: [
“Check % stocks above 200MA (StockCharts $NAA200R)”,
“> 55% = full 6 contracts”,
“40-55% = reduce to 4 contracts (Tension sizing)”,
“< 40% = reduce to 4 contracts”,
],
},
“TENSION”: {
“color”: “#d97706”, “bg”: “#1c1000”, “border”: “#92400e”,
“icon”: “~~”,
“contracts”: “4 contracts”, “dte”: “14-21 DTE”, “delta”: “0.15-0.20”,
“instrument”: “TQQQ cash-secured puts (further OTM)”,
“action”: “Reduced engine. Wider DTE for buffer. Monitor 200MA daily.”,
“checks”: [
“Watch for QQQ reclaiming or losing 200MA”,
“VIX rising toward 28+ = reduce to 2 contracts”,
],
},
“NO MAN’S LAND”: {
“color”: “#d97706”, “bg”: “#1c1000”, “border”: “#92400e”,
“icon”: “??”,
“contracts”: “0 unless filter passes”, “dte”: “7-10 DTE”, “delta”: “0.20-0.30”,
“instrument”: “SPXS or SQQQ credit spreads only”,
“action”: “DO NOT TRADE unless spread credit >= 25% of width. Median 2-day cluster.”,
“checks”: [
“Run credit filter before any entry”,
“Net credit >= 25% of spread width required”,
“If no spread passes the filter: stay cash”,
],
},
“STRESS”: {
“color”: “#dc2626”, “bg”: “#1a0000”, “border”: “#7f1d1d”,
“icon”: “DN”,
“contracts”: “0 new TQQQ CSPs”, “dte”: “7-10 DTE”, “delta”: “0.20-0.30”,
“instrument”: “SPXS spreads (preferred) / SQQQ credit spreads”,
“action”: “Inverse spread engine. No new TQQQ positions.”,
“checks”: [
“Credit filter: net credit >= 25% of width”,
“Short leg >= 5% OTM from current SPXS/SQQQ price”,
“Max collateral per spread: 15% of NLV”,
“SQQQ valid for spreads at any account size”,
],
},
“CAPITULATION”: {
“color”: “#7c3aed”, “bg”: “#0d0520”, “border”: “#4c1d95”,
“icon”: “!!”,
“contracts”: “0 new CSPs”, “dte”: “30-45 DTE (wildcard only)”, “delta”: “~0.35”,
“instrument”: “No CSPs. Wildcard: TQQQ long calls if all triggers met.”,
“action”: “VIX > 40. Minimal size. Wildcard eligible if all 5 triggers confirmed.”,
“checks”: [
“QQQ RSI < 25 on daily chart”,
“VIX9D declining while VIX still elevated”,
“2+ consecutive positive A/D days”,
“Entry 2+ days after VIX peak - NOT at peak itself”,
“Max wildcard: 2% of NLV per trade”,
],
},
}

# Data fetching

@st.cache_data(ttl=300)
def fetch_data(api_key, secret_key):
try:
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

```
    client = StockHistoricalDataClient(api_key, secret_key)
    et = ZoneInfo("America/New_York")
    end = datetime.now(et)
    start = end - timedelta(days=320)

    def get_closes(symbol, limit=220):
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
        )
        bars = client.get_stock_bars(req)
        df = bars.df
        if symbol in df.index.get_level_values(0):
            df = df.loc[symbol]
        df = df.sort_index()
        closes = [float(row["close"]) for _, row in df.iterrows()]
        dates  = [str(idx.date()) for idx in df.index]
        return closes[-limit:], dates[-limit:]

    qqq_closes, qqq_dates = get_closes("QQQ", 220)

    vix_raw = None
    vix_sym = None
    for sym in ["VIXY", "VXX"]:
        try:
            closes, _ = get_closes(sym, 10)
            if closes:
                vix_raw = closes[-1]
                vix_sym = sym
                break
        except Exception:
            continue

    if vix_raw is None:
        raise ValueError("Could not fetch VIX proxy (VIXY/VXX)")

    vix_est = round(vix_raw * 3.5, 1)

    return {
        "qqq_closes": qqq_closes,
        "qqq_date":   qqq_dates[-1] if qqq_dates else "N/A",
        "qqq_price":  qqq_closes[-1],
        "sma200":     calc_sma(qqq_closes, 200),
        "rsi14":      calc_rsi(qqq_closes, 14),
        "vix":        vix_est,
        "vix_sym":    vix_sym,
        "error":      None,
    }

except Exception as e:
    return {"error": str(e)}
```

# UI

st.markdown(”### V10 Morning Regime Check”)
et_now = datetime.now(ZoneInfo(“America/New_York”))
st.caption(et_now.strftime(”%A, %B %d, %Y  -  %I:%M %p ET”))
st.divider()

# API Keys - reads from Streamlit Secrets first, falls back to manual entry

api_key    = st.secrets.get(“ALPACA_API_KEY”, “”)
secret_key = st.secrets.get(“ALPACA_SECRET_KEY”, “”)

if not api_key or not secret_key:
with st.expander(“API Keys (not set in Secrets)”, expanded=True):
api_key    = st.text_input(“Alpaca API Key ID”, type=“default”)
secret_key = st.text_input(“Alpaca Secret Key”, type=“password”)
st.caption(“To avoid entering keys every time: add them to Streamlit Secrets in your app settings.”)

# Run button

if st.button(“Run Regime Check”):
if not api_key or not secret_key:
st.error(“Enter your Alpaca API keys above first.”)
else:
with st.spinner(“Fetching live data from Alpaca…”):
data = fetch_data(api_key, secret_key)

```
    if data.get("error"):
        st.error(f"Error: {data['error']}")
        st.info("Common fixes: check your API keys, make sure markets have traded recently.")
    else:
        qqq   = data["qqq_price"]
        sma   = data["sma200"]
        vix   = data["vix"]
        rsi   = data["rsi14"]
        regime, pct_vs_sma = determine_regime(qqq, sma, vix)
        cfg   = REGIMES[regime]
        above = qqq > sma

        sign = "+" if pct_vs_sma >= 0 else ""

        # Regime banner
        st.markdown(f"""
```

<div class="regime-card" style="background:{cfg['bg']};border-color:{cfg['border']}">
  <div style="font-size:0.75rem;color:{cfg['color']};letter-spacing:0.15em;margin-bottom:4px">REGIME</div>
  <div style="font-size:2rem;font-weight:900;color:{cfg['color']}">{cfg['icon']} {regime}</div>
  <div style="color:#94a3b8;font-size:0.85rem;margin-top:8px">{cfg['action']}</div>
</div>
""", unsafe_allow_html=True)

```
        # Market data
        st.markdown("#### Market Data")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"""
```

<div class="metric-card">
  <div class="metric-label">QQQ</div>
  <div class="metric-value">${qqq:.2f}</div>
  <div style="color:#64748b;font-size:0.75rem">{data['qqq_date']}</div>
</div>""", unsafe_allow_html=True)
                color_sma = "#4ade80" if above else "#f87171"
                st.markdown(f"""
<div class="metric-card">
  <div class="metric-label">vs 200MA</div>
  <div class="metric-value" style="color:{color_sma}">{sign}{pct_vs_sma:.2f}%</div>
  <div style="color:#64748b;font-size:0.75rem">200MA = ${sma:.2f}</div>
</div>""", unsafe_allow_html=True)
            with col2:
                vix_color = "#f87171" if vix > 30 else "#fbbf24" if vix > 20 else "#4ade80"
                st.markdown(f"""
<div class="metric-card">
  <div class="metric-label">VIX (est)</div>
  <div class="metric-value" style="color:{vix_color}">{vix:.1f}</div>
  <div style="color:#64748b;font-size:0.75rem">via {data['vix_sym']} x3.5</div>
</div>""", unsafe_allow_html=True)
                rsi_color = "#f87171" if rsi and rsi < 30 else "#fbbf24" if rsi and rsi > 70 else "#94a3b8"
                st.markdown(f"""
<div class="metric-card">
  <div class="metric-label">QQQ RSI(14)</div>
  <div class="metric-value" style="color:{rsi_color}">{rsi if rsi else 'N/A'}</div>
  <div style="color:#64748b;font-size:0.75rem">daily</div>
</div>""", unsafe_allow_html=True)

```
        st.caption("VIX is estimated from ETF proxy. Verify on CBOE if near a boundary (18-22 or 38-42).")

        # Playbook
        st.markdown("#### Today's Playbook")
        for label, val in [
            ("Instrument", cfg["instrument"]),
            ("Contracts",  cfg["contracts"]),
            ("DTE",        cfg["dte"]),
            ("Delta",      cfg["delta"]),
        ]:
            st.markdown(f"""
```

<div class="check-item" style="display:flex;justify-content:space-between">
  <span style="color:#64748b">{label}</span>
  <span style="color:#e2e8f0;font-weight:600;text-align:right">{val}</span>
</div>""", unsafe_allow_html=True)

```
        # Checks
        st.markdown("#### Before Trading")
        for check in cfg["checks"]:
            st.markdown(f"""
```

<div class="check-item">
  <span style="color:{cfg['color']};margin-right:8px">&gt;</span>{check}
</div>""", unsafe_allow_html=True)

```
        # Exit checklist
        st.markdown("#### Open Position Exit Check")
        for item in [
            "Has the option/spread doubled in value? (stop)",
            "Has the short strike been breached? (ITM)",
            "Has QQQ confirmed a regime change?",
            "Has the 50% profit GTC order filled?",
        ]:
            st.markdown(f"""
```

<div class="check-item">[ ] &nbsp;{item}</div>
""", unsafe_allow_html=True)

```
        st.success("Report generated at " + et_now.strftime("%I:%M %p ET"))
```

st.divider()
st.caption(“V10 Playbook - For personal use only - Not financial advice”)
