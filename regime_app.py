import streamlit as st
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

st.set_page_config(page_title="V10 Regime Check", page_icon="📊", layout="centered")

# Hide GitHub icon and toolbar, light theme

st.markdown("""

<style>
header[data-testid="stHeader"] {display:none}
#MainMenu {display:none}
footer {display:none}
.stDeployButton {display:none}
.stApp {background-color:#f8fafc}
.block-container {padding:1rem 1rem 2rem;max-width:500px}
h1,h2,h3,h4 {color:#1e293b !important}
p,li,label {color:#334155}
.metric-card {
    background:#ffffff;border:1px solid #e2e8f0;
    border-radius:10px;padding:12px;margin-bottom:8px;
    box-shadow:0 1px 3px rgba(0,0,0,0.06)
}
.metric-label {color:#64748b;font-size:0.72rem;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:2px}
.metric-value {color:#0f172a;font-size:1.2rem;font-weight:700;font-family:monospace}
.check-item {
    background:#ffffff;border:1px solid #e2e8f0;
    border-radius:8px;padding:10px 12px;margin-bottom:6px;
    color:#475569;font-size:0.85rem;
    box-shadow:0 1px 2px rgba(0,0,0,0.04)
}
.regime-card {border-radius:14px;padding:20px;margin-bottom:16px;border:2px solid}
.stButton>button {
    width:100%;border:none;border-radius:12px;
    padding:14px;font-size:1rem;font-weight:700;cursor:pointer
}
.copy-box {
    background:#f1f5f9;border:1px solid #cbd5e1;border-radius:10px;
    padding:14px;font-size:0.78rem;font-family:monospace;
    color:#334155;white-space:pre-wrap;line-height:1.6;margin-top:8px
}
</style>

""", unsafe_allow_html=True)

def calc_sma(prices, period):
if len(prices) < period:
return None
return sum(prices[-period:]) / period

def calc_rsi(prices, period=14):
if len(prices) < period + 1:
return None
gains = []
losses = []
for i in range(len(prices) - period, len(prices)):
d = prices[i] - prices[i - 1]
gains.append(max(d, 0))
losses.append(max(-d, 0))
ag = sum(gains) / period
al = sum(losses) / period
if al == 0:
return 100.0
return round(100 - 100 / (1 + ag / al), 1)

def determine_regime(qqq, sma200, vix):
above = qqq > sma200
pct = (qqq - sma200) / sma200 * 100
if vix > 40:
return "CAPITULATION", pct
if not above:
pct_below = (sma200 - qqq) / sma200 * 100
if pct_below <= 5 and 20 <= vix <= 28:
return "NO MAN’S LAND", pct
return "STRESS", pct
if vix < 20:
return "TREND", pct
return "TENSION", pct

REGIMES = {
"TREND": {
"color": "#16a34a", "bg": "#f0fdf4", "border": "#86efac", "icon": "TREND",
"contracts": "6 contracts", "dte": "7 DTE", "delta": "0.20-0.30",
"instrument": "TQQQ cash-secured puts",
"action": "Full CSP engine active. Set GTC profit-take at 50% immediately after fill.",
"checks": [
"Check % stocks above 200MA (StockCharts $NAA200R)",
"> 55% = full 6 contracts",
"40-55% = reduce to 4 contracts",
"< 40% = reduce to 4 contracts",
],
},
"TENSION": {
"color": "#d97706", "bg": "#fffbeb", "border": "#fcd34d", "icon": "TENSION",
"contracts": "4 contracts", "dte": "14-21 DTE", "delta": "0.15-0.20",
"instrument": "TQQQ cash-secured puts (further OTM)",
"action": "Reduced engine. Wider DTE. Monitor 200MA daily.",
"checks": [
"Watch for QQQ reclaiming or losing 200MA",
"VIX rising toward 28+ = reduce to 2 contracts",
],
},
"NO MAN’S LAND": {
"color": "#d97706", "bg": "#fffbeb", "border": "#fcd34d", "icon": "NO MAN’S LAND",
"contracts": "0 unless filter passes", "dte": "7-10 DTE", "delta": "0.20-0.30",
"instrument": "SPXS or SQQQ credit spreads only",
"action": "DO NOT TRADE unless spread credit >= 25% of width.",
"checks": [
"Run credit filter before any entry",
"Net credit >= 25% of spread width required",
"If no spread passes: stay cash",
],
},
"STRESS": {
"color": "#dc2626", "bg": "#fef2f2", "border": "#fca5a5", "icon": "STRESS",
"contracts": "0 new TQQQ CSPs", "dte": "7-10 DTE", "delta": "0.20-0.30",
"instrument": "SPXS spreads (preferred) / SQQQ credit spreads",
"action": "Inverse spread engine. No new TQQQ positions.",
"checks": [
"Credit filter: net credit >= 25% of width",
"Short leg >= 5% OTM from current price",
"Max collateral per spread: 15% of NLV",
],
},
"CAPITULATION": {
"color": "#7c3aed", "bg": "#faf5ff", "border": "#c4b5fd", "icon": "CAPITULATION",
"contracts": "0 new CSPs", "dte": "30-45 DTE wildcard only", "delta": "~0.35",
"instrument": "No CSPs. Wildcard: TQQQ long calls if all triggers met.",
"action": "VIX > 40. Minimal size. Wildcard eligible if all 5 triggers confirmed.",
"checks": [
"QQQ RSI < 25 on daily",
"VIX9D declining while VIX still elevated",
"2+ consecutive positive A/D days",
"Entry 2+ days after VIX peak - NOT at peak",
"Max wildcard: 2% of NLV per trade",
],
},
}

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
            feed="iex",
        )
        bars = client.get_stock_bars(req)
        df = bars.df
        if symbol in df.index.get_level_values(0):
            df = df.loc[symbol]
        df = df.sort_index()
        closes = [float(row["close"]) for _, row in df.iterrows()]
        dates = [str(idx.date()) for idx in df.index]
        return closes[-limit:], dates[-limit:]

    qqq_closes, qqq_dates = get_closes("QQQ", 220)

    # VIX: try the index directly first, then ETF proxies with no scaling
    # VIXY and VXX trade near VIX levels directly — no multiplier needed
    vix_price = None
    vix_sym = None
    vix_note = ""

    # Try actual VIX index
    for sym in ["VIX"]:
        try:
            closes, _ = get_closes(sym, 10)
            if closes:
                vix_price = closes[-1]
                vix_sym = "VIX"
                vix_note = "direct"
                break
        except Exception:
            pass

    # Fall back to ETF proxies — VIXY and VXX trade in similar range to VIX, no scaling
    if vix_price is None:
        for sym in ["VIXY", "VXX"]:
            try:
                closes, _ = get_closes(sym, 10)
                if closes:
                    vix_price = closes[-1]
                    vix_sym = sym
                    vix_note = "ETF proxy, verify on CBOE"
                    break
            except Exception:
                continue

    if vix_price is None:
        raise ValueError("Could not fetch VIX data (tried VIX, VIXY, VXX)")

    return {
        "qqq_closes": qqq_closes,
        "qqq_date": qqq_dates[-1] if qqq_dates else "N/A",
        "qqq_price": qqq_closes[-1],
        "sma200": calc_sma(qqq_closes, 200),
        "rsi14": calc_rsi(qqq_closes, 14),
        "vix": round(vix_price, 1),
        "vix_sym": vix_sym,
        "vix_note": vix_note,
        "error": None,
    }

except Exception as e:
    return {"error": str(e)}
```

def build_ai_summary(regime, qqq, sma, pct, vix, vix_sym, rsi, cfg, et_now):
sign = "+" if pct >= 0 else ""
above = "ABOVE" if pct >= 0 else "BELOW"
lines = [
"=== V10 MORNING REGIME REPORT ===",
et_now.strftime("%A, %B %d, %Y  %I:%M %p ET"),
"",
f"REGIME: {regime}",
f"Action: {cfg[‘action’]}",
"",
"— MARKET DATA —",
f"QQQ:        ${qqq:.2f}",
f"200-day SMA: ${sma:.2f}",
f"QQQ vs SMA:  {sign}{pct:.2f}% ({above})",
f"VIX (est):   {vix:.1f}  [via {vix_sym}]",
f"QQQ RSI(14): {rsi}",
"",
"— TODAY’S PLAYBOOK —",
f"Instrument: {cfg[‘instrument’]}",
f"Contracts:  {cfg[‘contracts’]}",
f"DTE:        {cfg[‘dte’]}",
f"Delta:      {cfg[‘delta’]}",
"",
"— BEFORE TRADING —",
]
for c in cfg["checks"]:
lines.append(f"  - {c}")
lines += [
"",
"— OPEN POSITION EXIT CHECK —",
"  [ ] Has the option/spread doubled in value? (stop)",
"  [ ] Has the short strike been breached? (ITM)",
"  [ ] Has QQQ confirmed a regime change?",
"  [ ] Has the 50% profit GTC order filled?",
"",
"NOTE: VIX is estimated. Verify on CBOE if near boundary (18-22 or 38-42).",
]
return "\n".join(lines)

# ── UI ────────────────────────────────────────────────────────────────────────

st.markdown("### V10 Morning Regime Check")
et_now = datetime.now(ZoneInfo("America/New_York"))
st.caption(et_now.strftime("%A, %B %d, %Y  -  %I:%M %p ET"))
st.divider()

# Keys

api_key = st.secrets.get("ALPACA_API_KEY", "")
secret_key = st.secrets.get("ALPACA_SECRET_KEY", "")

if not api_key or not secret_key:
with st.expander("API Keys", expanded=True):
api_key = st.text_input("Alpaca API Key ID")
secret_key = st.text_input("Alpaca Secret Key", type="password")
st.caption("Add keys to Streamlit Secrets to avoid entering them every time.")

if st.button("Run Regime Check", type="primary"):
if not api_key or not secret_key:
st.error("Enter your Alpaca API keys first.")
else:
with st.spinner("Fetching live data…"):
data = fetch_data(api_key, secret_key)

```
    if data.get("error"):
        st.error("Error: " + data["error"])
        st.info("Check your API keys and try again.")
    else:
        qqq = data["qqq_price"]
        sma = data["sma200"]
        vix = data["vix"]
        rsi = data["rsi14"]
        regime, pct = determine_regime(qqq, sma, vix)
        cfg = REGIMES[regime]
        above = qqq > sma
        sign = "+" if pct >= 0 else ""

        # Regime banner
        st.markdown(
            '<div class="regime-card" style="background:' + cfg["bg"]
            + ';border-color:' + cfg["border"] + '">'
            + '<div style="font-size:0.72rem;color:' + cfg["color"]
            + ';letter-spacing:0.12em;margin-bottom:4px">REGIME</div>'
            + '<div style="font-size:2rem;font-weight:900;color:' + cfg["color"]
            + '">' + cfg["icon"] + "</div>"
            + '<div style="color:#475569;font-size:0.85rem;margin-top:8px">'
            + cfg["action"] + "</div></div>",
            unsafe_allow_html=True,
        )

        # Market data
        st.markdown("#### Market Data")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(
                '<div class="metric-card"><div class="metric-label">QQQ</div>'
                + '<div class="metric-value">$' + f"{qqq:.2f}" + "</div>"
                + '<div style="color:#94a3b8;font-size:0.72rem">' + data["qqq_date"] + "</div></div>",
                unsafe_allow_html=True,
            )
            sma_color = "#16a34a" if above else "#dc2626"
            st.markdown(
                '<div class="metric-card"><div class="metric-label">vs 200MA</div>'
                + '<div class="metric-value" style="color:' + sma_color + '">'
                + sign + f"{pct:.2f}%" + "</div>"
                + '<div style="color:#94a3b8;font-size:0.72rem">SMA = $' + f"{sma:.2f}" + "</div></div>",
                unsafe_allow_html=True,
            )
        with col2:
            vix_color = "#dc2626" if vix > 30 else "#d97706" if vix > 20 else "#16a34a"
            st.markdown(
                '<div class="metric-card"><div class="metric-label">VIX</div>'
                + '<div class="metric-value" style="color:' + vix_color + '">'
                + f"{vix:.1f}" + "</div>"
                + '<div style="color:#94a3b8;font-size:0.72rem">via ' + data["vix_sym"] + "</div></div>",
                unsafe_allow_html=True,
            )
            rsi_val = str(rsi) if rsi else "N/A"
            rsi_color = "#dc2626" if rsi and rsi < 30 else "#d97706" if rsi and rsi > 70 else "#334155"
            st.markdown(
                '<div class="metric-card"><div class="metric-label">QQQ RSI(14)</div>'
                + '<div class="metric-value" style="color:' + rsi_color + '">'
                + rsi_val + "</div>"
                + '<div style="color:#94a3b8;font-size:0.72rem">daily</div></div>',
                unsafe_allow_html=True,
            )

        if data["vix_note"] != "direct":
            st.caption("VIX is an estimate. Verify on CBOE if near 18-22 or 38-42.")

        # Playbook
        st.markdown("#### Today's Playbook")
        for label, val in [
            ("Instrument", cfg["instrument"]),
            ("Contracts", cfg["contracts"]),
            ("DTE", cfg["dte"]),
            ("Delta", cfg["delta"]),
        ]:
            st.markdown(
                '<div class="check-item" style="display:flex;justify-content:space-between;gap:8px">'
                + '<span style="color:#64748b;flex-shrink:0">' + label + "</span>"
                + '<span style="color:#1e293b;font-weight:600;text-align:right">' + val + "</span></div>",
                unsafe_allow_html=True,
            )

        # Checks
        st.markdown("#### Before Trading")
        for check in cfg["checks"]:
            st.markdown(
                '<div class="check-item">'
                + '<span style="color:' + cfg["color"] + ';margin-right:8px;font-weight:700">&gt;</span>'
                + check + "</div>",
                unsafe_allow_html=True,
            )

        # Exit checklist
        st.markdown("#### Open Position Exit Check")
        for item in [
            "Has the option/spread doubled in value? (stop)",
            "Has the short strike been breached? (ITM)",
            "Has QQQ confirmed a regime change?",
            "Has the 50% profit GTC order filled?",
        ]:
            st.markdown(
                '<div class="check-item">[ ] &nbsp;' + item + "</div>",
                unsafe_allow_html=True,
            )

        # Copy for AI
        st.markdown("#### Copy for AI")
        st.caption("Tap the text below, select all, and paste into Claude or Compa.")
        summary = build_ai_summary(regime, qqq, sma, pct, vix, data["vix_sym"], rsi, cfg, et_now)
        st.text_area("", value=summary, height=320, label_visibility="collapsed")

        st.success("Report generated at " + et_now.strftime("%I:%M %p ET"))
```

st.divider()
st.caption("V10 Playbook - For personal use only - Not financial advice")