import streamlit as st
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math

st.set_page_config(page_title="V10 Regime Check", page_icon="📊", layout="centered")

st.markdown("""
<style>
header[data-testid="stHeader"] {display:none}
#MainMenu {display:none}
footer {display:none}
.stDeployButton {display:none}
.stApp {background-color:#f8fafc}
.block-container {padding:1rem 1rem 2rem;max-width:520px}
h1,h2,h3,h4 {color:#1e293b !important}
p,li {color:#334155}
.regime-card {border-radius:14px;padding:20px;margin-bottom:12px;border:2px solid}
.section-card {
    background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;
    padding:16px;margin-bottom:10px;box-shadow:0 1px 3px rgba(0,0,0,0.04)
}
.section-title {
    font-size:0.68rem;font-weight:700;letter-spacing:0.12em;
    text-transform:uppercase;color:#64748b;margin-bottom:10px
}
.row {
    display:flex;justify-content:space-between;align-items:center;
    padding:7px 0;border-bottom:1px solid #f1f5f9;gap:8px
}
.row:last-child {border-bottom:none}
.row-label {color:#64748b;font-size:0.83rem;flex-shrink:0}
.row-value {color:#1e293b;font-size:0.83rem;font-weight:600;text-align:right}
.pill {display:inline-block;padding:2px 10px;border-radius:99px;font-size:0.72rem;font-weight:700}
.pill-green {background:#dcfce7;color:#166534}
.pill-red {background:#fee2e2;color:#991b1b}
.pill-yellow {background:#fef9c3;color:#854d0e}
.pill-orange {background:#ffedd5;color:#9a3412}
.pill-gray {background:#f1f5f9;color:#475569}
.pill-blue {background:#dbeafe;color:#1e40af}
.metric-grid {display:grid;grid-template-columns:1fr 1fr;gap:8px}
.metric-box {background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px}
.metric-label {color:#64748b;font-size:0.68rem;letter-spacing:0.08em;text-transform:uppercase}
.metric-value {color:#0f172a;font-size:1.1rem;font-weight:700;font-family:monospace}
.signal-bar {
    border-radius:10px;padding:12px 16px;margin-bottom:10px;
    display:flex;align-items:center;gap:12px;
}
.calc-pass {background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:12px;margin-top:6px}
.calc-warn {background:#fef9c3;border:1px solid #fde047;border-radius:8px;padding:12px;margin-top:6px}
.calc-fail {background:#fee2e2;border:1px solid #fca5a5;border-radius:8px;padding:12px;margin-top:6px}
.vix-warn {background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:8px 12px;margin-bottom:8px;font-size:0.78rem;color:#9a3412}
.stButton>button {
    width:100%;border:none;border-radius:12px;
    padding:14px;font-size:1rem;font-weight:700;cursor:pointer
}
</style>
""", unsafe_allow_html=True)


# ── Calculations ──────────────────────────────────────────────────────────────
def calc_sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(len(prices) - period, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 1)


# ── Regime engine ─────────────────────────────────────────────────────────────
def determine_regime(qqq, sma200, vix, vix_reliable, breadth, gamma_above_flip):
    above = qqq > sma200
    pct = (qqq - sma200) / sma200 * 100

    # Q1: primary gate
    if vix > 40 and vix_reliable:
        return "CAPITULATION", pct, "HIGH"
    if not above:
        pct_below = (sma200 - qqq) / sma200 * 100
        if pct_below <= 5 and 20 <= vix <= 28:
            return "NO MAN'S LAND", pct, "MEDIUM"
        return "STRESS", pct, "HIGH"

    # Above 200MA — check VIX
    if vix_reliable:
        if vix > 40:
            return "CAPITULATION", pct, "HIGH"
        if vix >= 20:
            regime = "TENSION"
        else:
            regime = "TREND"
    else:
        # VIX unreliable — default to TENSION if above 200MA
        regime = "TENSION"

    # Confidence scoring
    bull_signals = 0
    bear_signals = 0
    if above:
        bull_signals += 1
    if vix_reliable and vix < 20:
        bull_signals += 1
    if gamma_above_flip:
        bull_signals += 1
    if breadth is not None and breadth >= 55:
        bull_signals += 1
    elif breadth is not None and breadth < 40:
        bear_signals += 1
        if regime == "TREND":
            regime = "TENSION"  # breadth overrides Trend upgrade

    if bull_signals >= 3 and bear_signals == 0:
        confidence = "HIGH"
    elif bear_signals >= 1:
        confidence = "LOW"
    else:
        confidence = "MEDIUM"

    return regime, pct, confidence


def execution_signal(regime, confidence, vix_reliable):
    if regime == "STRESS":
        return "CAUTION", "orange", "Inverse spreads only. Verify filter before entry."
    if regime == "CAPITULATION":
        return "HOLD", "red", "No CSPs. Wildcard only if all 5 triggers confirmed."
    if regime == "NO MAN'S LAND":
        return "HOLD", "red", "Stay cash unless spread passes credit filter."
    if regime == "TREND" and confidence == "HIGH":
        return "GO", "green", "Full Trend deployment. Set GTC immediately."
    if regime == "TREND" and confidence == "MEDIUM":
        return "CAUTION", "yellow", "Trend conditions but watch breadth."
    if regime == "TENSION" and confidence == "LOW":
        return "CAUTION", "orange", "Fragile Tension. Reduce deployment, stay OTM."
    if regime == "TENSION":
        return "CAUTION", "yellow", "Tension. Mid-sized deployment, wider DTE."
    return "HOLD", "gray", "Assess conditions before trading."


def suggested_deployment(regime, confidence, breadth):
    if regime == "TREND":
        if confidence == "HIGH":
            return 65, "Full cap — breadth and VIX confirmed"
        if breadth is not None and breadth >= 50:
            return 55, "Near full — breadth approaching confirmation"
        return 50, "Reduced — breadth not fully confirmed"
    if regime == "TENSION":
        # V10 Tension cap is 45%. Breadth reduces within range, never below 40%.
        if breadth is not None and breadth < 40:
            return 40, "Weak breadth — reduced but within Tension cap"
        return 45, "Standard Tension cap — watch breadth for Trend upgrade"
    return 0, "No bullish deployment allowed"


def strike_zone(tqqq_price, put_wall, gamma_flip):
    """
    Strike zone is based on current price and put wall, not gamma flip.
    Gamma flip is a risk threshold - we sell below the put wall which is
    where dealer support exists. Target: 2-5% OTM from current price,
    anchored below put wall if one is provided.
    """
    if tqqq_price is None:
        return None, None
    # Primary: use put wall as upper anchor (sell below it)
    if put_wall and put_wall < tqqq_price:
        sz_high = put_wall
        sz_low = round(put_wall - 1.5, 1)
        return sz_low, sz_high
    # Fallback: 3-5% OTM from current price
    sz_high = round(tqqq_price * 0.97, 1)
    sz_low = round(tqqq_price * 0.95, 1)
    return sz_low, sz_high


# ── Regime config ─────────────────────────────────────────────────────────────
REGIMES = {
    "TREND": {
        "color": "#16a34a", "bg": "#f0fdf4", "border": "#86efac",
        "bullish_csp": True, "inverse": False, "wildcard": False,
        "max_cap": 65, "dte": "7 DTE", "delta": "0.20-0.30",
        "instrument": "TQQQ cash-secured puts",
        "engines": {"Trend CSP": True, "Tension CSP": False, "Stress/Inverse": False, "Wildcard": False},
        "checks": [
            "Confirm % stocks above 200MA > 55% before full deployment",
            "Set GTC profit-take at 50% immediately after fill",
            "90-minute re-entry delay after any close",
        ],
        "cash_buffer": "MAX(2x total premium, 5% of NLV)",
    },
    "TENSION": {
        "color": "#d97706", "bg": "#fffbeb", "border": "#fcd34d",
        "bullish_csp": True, "inverse": False, "wildcard": False,
        "max_cap": 45, "dte": "14-21 DTE", "delta": "0.15-0.20",
        "instrument": "TQQQ cash-secured puts (further OTM)",
        "engines": {"Trend CSP": False, "Tension CSP": True, "Stress/Inverse": False, "Wildcard": False},
        "checks": [
            "Watch for sustained hold above 200MA (2-3 days) before upgrading",
            "Watch for rejection near recent highs",
            "VIX > 25 = reduce deployment",
            "If transitioning from Stress: Day 0 observe only, deploy Day 1",
        ],
        "cash_buffer": "MAX(2x total premium, 5% of NLV)",
    },
    "NO MAN'S LAND": {
        "color": "#d97706", "bg": "#fffbeb", "border": "#fcd34d",
        "bullish_csp": False, "inverse": True, "wildcard": False,
        "max_cap": 0, "dte": "7-10 DTE", "delta": "0.20-0.30",
        "instrument": "SPXS or SQQQ credit spreads only",
        "engines": {"Trend CSP": False, "Tension CSP": False, "Stress/Inverse": True, "Wildcard": False},
        "checks": [
            "Credit filter: net credit >= 25% of spread width",
            "Short leg >= 5% OTM from current price",
            "If no spread passes filter: stay 100% cash",
        ],
        "cash_buffer": "100% cash unless filter passes",
    },
    "STRESS": {
        "color": "#dc2626", "bg": "#fef2f2", "border": "#fca5a5",
        "bullish_csp": False, "inverse": True, "wildcard": False,
        "max_cap": 0, "dte": "7-10 DTE", "delta": "0.20-0.30",
        "instrument": "SPXS spreads (preferred) / SQQQ credit spreads",
        "engines": {"Trend CSP": False, "Tension CSP": False, "Stress/Inverse": True, "Wildcard": False},
        "checks": [
            "Credit filter: net credit >= 25% of spread width",
            "Short leg >= 5% OTM from current price",
            "Max spread collateral: 15% of NLV per position",
        ],
        "cash_buffer": "MAX(2x total premium, 5% of NLV)",
    },
    "CAPITULATION": {
        "color": "#7c3aed", "bg": "#faf5ff", "border": "#c4b5fd",
        "bullish_csp": False, "inverse": False, "wildcard": True,
        "max_cap": 0, "dte": "30-45 DTE (wildcard only)", "delta": "~0.35",
        "instrument": "No CSPs. Wildcard: TQQQ long calls if all triggers met.",
        "engines": {"Trend CSP": False, "Tension CSP": False, "Stress/Inverse": False, "Wildcard": True},
        "checks": [
            "QQQ RSI < 25 on daily",
            "VIX9D declining while VIX still elevated",
            "2+ consecutive positive A/D days",
            "Entry 2+ days after VIX peak - NOT at peak",
            "Max wildcard: 2% of NLV per trade, 5% total",
        ],
        "cash_buffer": "MAX(wildcard cost, 5% of NLV)",
    },
}


# ── Data fetch ────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_data(api_key, secret_key):
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(api_key, secret_key)
        et = ZoneInfo("America/New_York")
        end = datetime.now(et)
        start = end - timedelta(days=320)

        def get_closes(symbol, limit=220):
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start, end=end, feed="iex",
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

        # TQQQ for current price reference
        tqqq_price = None
        try:
            tqqq_closes, _ = get_closes("TQQQ", 5)
            if tqqq_closes:
                tqqq_price = tqqq_closes[-1]
        except Exception:
            pass

        # VIX: try direct index, flag reliability
        vix_price = None
        vix_sym = None
        vix_reliable = False
        for sym in ["VIX", "VIXY", "VXX"]:
            try:
                closes, _ = get_closes(sym, 10)
                if closes:
                    vix_price = closes[-1]
                    vix_sym = sym
                    vix_reliable = (sym == "VIX")
                    break
            except Exception:
                continue

        if vix_price is None:
            raise ValueError("Could not fetch VIX data")

        return {
            "qqq_closes": qqq_closes,
            "qqq_date": qqq_dates[-1] if qqq_dates else "N/A",
            "qqq_price": qqq_closes[-1],
            "sma200": calc_sma(qqq_closes, 200),
            "rsi14": calc_rsi(qqq_closes, 14),
            "vix": round(vix_price, 1),
            "vix_sym": vix_sym,
            "vix_reliable": vix_reliable,
            "tqqq_price": tqqq_price,
            "error": None,
        }
    except Exception as e:
        return {"error": str(e)}


# ── HTML helpers ──────────────────────────────────────────────────────────────
def pill(text, style="gray"):
    return f'<span class="pill pill-{style}">{text}</span>'

def row_html(label, value_html):
    return (f'<div class="row"><span class="row-label">{label}</span>'
            f'<span class="row-value">{value_html}</span></div>')

def section_html(title, rows):
    return (f'<div class="section-card"><div class="section-title">{title}</div>'
            + "".join(rows) + "</div>")


# ── AI summary ────────────────────────────────────────────────────────────────
def build_summary(regime, confidence, sig_label, qqq, sma, pct, vix, vix_sym,
                  vix_reliable, rsi, cfg, dep_pct, dep_note, breadth,
                  gamma_above, gamma_flip, put_wall, call_wall, tqqq_price,
                  sz_low, sz_high, et_now):
    sign = "+" if pct >= 0 else ""
    above = "ABOVE" if pct >= 0 else "BELOW"
    on_off = lambda v: "ON" if v else "OFF"
    e = cfg["engines"]
    lines = [
        "=== V10 MORNING REGIME REPORT ===",
        et_now.strftime("%A, %B %d, %Y  %I:%M %p ET"),
        "",
        f"REGIME:           {regime}",
        f"CONFIDENCE:       {confidence}",
        f"EXECUTION SIGNAL: {sig_label}",
        "",
        "--- MARKET DATA ---",
        f"QQQ:         ${qqq:.2f}",
        f"200-day SMA: ${sma:.2f}",
        f"QQQ vs SMA:  {sign}{pct:.2f}% ({above})",
        f"VIX:         {vix:.1f} via {vix_sym}"
        + (" [CONFIRMED]" if vix_reliable else " [LOW CONFIDENCE - verify on CBOE]"),
        f"QQQ RSI(14): {rsi if rsi else 'N/A'}",
        "",
        "--- MARKET STRUCTURE ---",
        f"TQQQ Price:       ${tqqq_price:.2f}" if tqqq_price else "TQQQ Price:       N/A",
        f"Gamma Regime:     {'POSITIVE (above flip)' if gamma_above else 'NEGATIVE (below flip)' if gamma_above is not None else 'Not provided'}",
        f"Gamma Flip:       {gamma_flip if gamma_flip else 'Not provided'}",
        f"Put Wall:         {put_wall if put_wall else 'Not provided'}",
        f"Call Wall:        {call_wall if call_wall else 'Not provided'}",
        f"Breadth (>200MA): {str(breadth) + '%' if breadth is not None else 'Not provided'}"
        + (" [WEAK]" if breadth is not None and breadth < 40 else
           " [MODERATE]" if breadth is not None and breadth < 55 else
           " [STRONG]" if breadth is not None else ""),
        "",
        "--- PLAYBOOK OUTPUT ---",
        f"Bullish CSPs:     {'ALLOWED' if cfg['bullish_csp'] else 'NOT ALLOWED'}",
        f"Inverse Spreads:  {'ACTIVE' if cfg['inverse'] else 'OFF'}",
        f"Wildcard:         {'ELIGIBLE' if cfg['wildcard'] else 'NOT ELIGIBLE'}",
        f"Deployment Cap:   {dep_pct}% of NLV (assignment utilization)",
        f"Suggested Deploy: {dep_pct}% — {dep_note}",
        f"Cash Buffer Min:  {cfg['cash_buffer']}",
        f"Target DTE:       {cfg['dte']}",
        f"Target Delta:     {cfg['delta']}",
        f"Instrument:       {cfg['instrument']}",
    ]
    if sz_low and sz_high:
        lines.append(f"Strike Zone:      ${sz_low:.1f} – ${sz_high:.1f} (below put wall / gamma flip)")
    lines += [
        "",
        "--- ENGINES ---",
        f"Trend CSP:        {on_off(e['Trend CSP'])}",
        f"Tension CSP:      {on_off(e['Tension CSP'])}",
        f"Stress/Inverse:   {on_off(e['Stress/Inverse'])}",
        f"Wildcard:         {on_off(e['Wildcard'])}",
        "",
        "--- BEFORE TRADING ---",
    ]
    for c in cfg["checks"]:
        lines.append(f"  - {c}")
    lines += [
        "",
        "--- OPEN POSITION EXIT CHECK ---",
        "  [ ] Option/spread doubled in value? (stop)",
        "  [ ] Short strike breached? (ITM)",
        "  [ ] QQQ confirmed regime change?",
        "  [ ] 50% profit GTC order filled?",
        "",
        "NOTE: Contracts = FLOOR((NLV x Deployment%) / Strike).",
    ]
    if not vix_reliable:
        lines.append("NOTE: VIX is estimated — DO NOT use for threshold decisions. Verify on CBOE.")
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
        st.caption("Add to Streamlit Secrets to avoid entering every time.")

# Manual inputs — always visible
st.markdown("#### Manual Inputs")
st.caption("Enter from Barchart (gamma) and StockCharts (breadth). Leave 0 if unknown.")

col1, col2 = st.columns(2)
with col1:
    breadth = st.number_input("% Stocks Above 200MA", min_value=0, max_value=100, value=0, step=1)
    gamma_flip = st.number_input("TQQQ Gamma Flip", min_value=0.0, value=0.0, step=0.5)
    put_wall = st.number_input("TQQQ Put Wall", min_value=0.0, value=0.0, step=0.5)
with col2:
    call_wall = st.number_input("TQQQ Call Wall", min_value=0.0, value=0.0, step=0.5)
    vix_manual = st.number_input("VIX (manual override)", min_value=0.0, value=0.0, step=0.1,
                                  help="Enter real VIX from CBOE to override the ETF proxy estimate")

breadth_val = breadth if breadth > 0 else None
gamma_flip_val = gamma_flip if gamma_flip > 0 else None
put_wall_val = put_wall if put_wall > 0 else None
call_wall_val = call_wall if call_wall > 0 else None
vix_manual_val = vix_manual if vix_manual > 0 else None

if st.button("Run Regime Check", type="primary"):
    if not api_key or not secret_key:
        st.error("Enter your Alpaca API keys first.")
    else:
        with st.spinner("Fetching live data..."):
            fetched = fetch_data(api_key, secret_key)
        st.session_state["last_data"] = fetched

data = st.session_state.get("last_data")

if data:
    if data.get("error"):
        st.error("Error: " + data["error"])
    else:
        qqq = data["qqq_price"]
        sma = data["sma200"]
        rsi = data["rsi14"]
        tqqq_price = data.get("tqqq_price")

        # VIX: use manual override if provided, else fetched
        if vix_manual_val:
            vix = vix_manual_val
            vix_sym = "manual"
            vix_reliable = True
        else:
            vix = data["vix"]
            vix_sym = data["vix_sym"]
            vix_reliable = data["vix_reliable"]

        # Gamma above flip
        gamma_above = None
        if gamma_flip_val and tqqq_price:
            gamma_above = tqqq_price > gamma_flip_val
        elif gamma_flip_val and qqq:
            gamma_above = True  # rough assumption if no TQQQ

        # Run regime engine
        regime, pct, confidence = determine_regime(
            qqq, sma, vix, vix_reliable, breadth_val, gamma_above
        )
        cfg = REGIMES[regime]
        sig_label, sig_color, sig_note = execution_signal(regime, confidence, vix_reliable)
        dep_pct, dep_note = suggested_deployment(regime, confidence, breadth_val)
        above = qqq > sma
        sign = "+" if pct >= 0 else ""

        # Strike zone
        sz_low, sz_high = strike_zone(tqqq_price, put_wall_val, gamma_flip_val)

        # ── Execution Signal bar
        sig_colors = {
            "green": ("#166534", "#dcfce7", "#86efac"),
            "yellow": ("#854d0e", "#fef9c3", "#fde047"),
            "orange": ("#9a3412", "#fff7ed", "#fed7aa"),
            "red":    ("#991b1b", "#fee2e2", "#fca5a5"),
            "gray":   ("#374151", "#f9fafb", "#e5e7eb"),
        }
        sc = sig_colors.get(sig_color, sig_colors["gray"])
        st.markdown(
            f'<div class="signal-bar" style="background:{sc[1]};border:1.5px solid {sc[2]}">'
            f'<div style="font-size:1.4rem">{"🟢" if sig_color=="green" else "🟡" if sig_color=="yellow" else "🟠" if sig_color=="orange" else "🔴"}</div>'
            f'<div><div style="font-weight:800;color:{sc[0]};font-size:0.85rem">'
            f'EXECUTION SIGNAL: {sig_label}</div>'
            f'<div style="color:{sc[0]};font-size:0.78rem;margin-top:2px">{sig_note}</div></div></div>',
            unsafe_allow_html=True,
        )

        # ── Regime card
        conf_pill_style = "green" if confidence == "HIGH" else "yellow" if confidence == "MEDIUM" else "orange"
        st.markdown(
            f'<div class="regime-card" style="background:{cfg["bg"]};border-color:{cfg["border"]}">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">'
            f'<div style="font-size:0.68rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:{cfg["color"]}">REGIME</div>'
            f'{pill(confidence + " CONFIDENCE", conf_pill_style)}</div>'
            f'<div style="font-size:2rem;font-weight:900;color:{cfg["color"]};margin-bottom:8px">{regime}</div>'
            f'<div style="color:#475569;font-size:0.83rem">{cfg["action"]}</div></div>',
            unsafe_allow_html=True,
        )

        # ── VIX warning if unreliable
        if not vix_reliable:
            st.markdown(
                f'<div class="vix-warn">⚠️ VIX showing <strong>{vix:.1f}</strong> via {vix_sym} '
                f'(ETF proxy — LOW CONFIDENCE). Enter real VIX from CBOE in manual inputs above '
                f'to enable accurate threshold decisions.</div>',
                unsafe_allow_html=True,
            )

        # ── Market Data
        sma_color = "#16a34a" if above else "#dc2626"
        vix_color = "#dc2626" if vix > 30 else "#d97706" if vix > 20 else "#16a34a"
        rsi_str = str(rsi) if rsi else "N/A"
        rsi_color = "#dc2626" if rsi and rsi < 30 else "#d97706" if rsi and rsi > 70 else "#0f172a"

        metric_html = '<div class="metric-grid">'
        metrics = [
            ("QQQ", f"${qqq:.2f}", None, data["qqq_date"]),
            ("200-day SMA", f"${sma:.2f}", None, None),
            ("vs 200MA", f"{sign}{pct:.2f}%", sma_color, "above" if above else "below"),
            ("VIX" + (" ✓" if vix_reliable else " ~"), f"{vix:.1f}", vix_color,
             vix_sym + (" confirmed" if vix_reliable else " — verify CBOE")),
            ("QQQ RSI(14)", rsi_str, rsi_color, "daily"),
        ]
        if tqqq_price:
            metrics.insert(0, ("TQQQ", f"${tqqq_price:.2f}", None, None))

        for label, val, color, note in metrics:
            metric_html += (
                f'<div class="metric-box">'
                f'<div class="metric-label">{label}</div>'
                f'<div class="metric-value"' + (f' style="color:{color}"' if color else '') + f'>{val}</div>'
                + (f'<div style="color:#94a3b8;font-size:0.68rem">{note}</div>' if note else "")
                + "</div>"
            )
        metric_html += "</div>"
        st.markdown(
            f'<div class="section-card"><div class="section-title">Market Data  '
            f'<span style="font-weight:400">· QQQ as of {data["qqq_date"]}</span></div>'
            f'{metric_html}</div>',
            unsafe_allow_html=True,
        )

        # ── Market Structure (gamma + breadth)
        struct_rows = []
        if tqqq_price:
            struct_rows.append(row_html("TQQQ vs Gamma Flip",
                pill("ABOVE — positive gamma", "green") if gamma_above
                else pill("BELOW — negative gamma", "red") if gamma_above is not None
                else pill("Enter gamma flip", "gray")))
        if gamma_flip_val:
            struct_rows.append(row_html("Gamma Flip", f"${gamma_flip_val:.2f}"))
        if put_wall_val:
            struct_rows.append(row_html("Put Wall", f"${put_wall_val:.2f}"))
        if call_wall_val:
            struct_rows.append(row_html("Call Wall", f"${call_wall_val:.2f}"))
        if breadth_val is not None:
            b_style = "green" if breadth_val >= 55 else "yellow" if breadth_val >= 40 else "red"
            b_label = "STRONG" if breadth_val >= 55 else "MODERATE" if breadth_val >= 40 else "WEAK"
            struct_rows.append(row_html("Breadth (% > 200MA)",
                f'{pill(b_label, b_style)} <span style="margin-left:6px">{breadth_val}%</span>'))
        if sz_low and sz_high:
            struct_rows.append(row_html("Suggested Strike Zone",
                f'<span style="color:#166534;font-weight:700">${sz_low:.1f} – ${sz_high:.1f}</span>'))

        if struct_rows:
            st.markdown(section_html("Market Structure", struct_rows), unsafe_allow_html=True)

        # ── Playbook output
        csp_p = pill("ALLOWED", "green") if cfg["bullish_csp"] else pill("NOT ALLOWED", "red")
        inv_p = pill("ACTIVE", "green") if cfg["inverse"] else pill("OFF", "gray")
        wc_p = pill("ELIGIBLE", "yellow") if cfg["wildcard"] else pill("NOT ELIGIBLE", "gray")
        dep_disp = f"{dep_pct}% of NLV" if dep_pct > 0 else pill("0% — no bullish deployment", "red")
        dep_note_disp = f'<span style="color:#64748b;font-size:0.78rem">{dep_note}</span>'

        st.markdown(section_html("Playbook Output", [
            row_html("Bullish CSPs", csp_p),
            row_html("Inverse Spreads", inv_p),
            row_html("Wildcard", wc_p),
            row_html("Max Assignment Cap", f"{cfg['max_cap']}% of NLV"),
            row_html("Suggested Deployment", f"{dep_disp} {dep_note_disp}"),
            row_html("Cash Buffer Min", cfg["cash_buffer"]),
            row_html("Target DTE", cfg["dte"]),
            row_html("Target Delta", cfg["delta"]),
            row_html("Instrument", cfg["instrument"]),
        ]), unsafe_allow_html=True)

        # ── Engine status
        e = cfg["engines"]
        eng_p = lambda v: pill("ON", "green") if v else pill("OFF", "gray")
        st.markdown(section_html("Engine Status", [
            row_html("Trend CSP", eng_p(e["Trend CSP"])),
            row_html("Tension CSP", eng_p(e["Tension CSP"])),
            row_html("Stress / Inverse", eng_p(e["Stress/Inverse"])),
            row_html("Wildcard", eng_p(e["Wildcard"])),
        ]), unsafe_allow_html=True)

        # ── Before trading
        checks_html = "".join([
            f'<div style="padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:0.83rem;color:#475569">'
            f'<span style="color:{cfg["color"]};margin-right:8px;font-weight:700">&rsaquo;</span>{c}</div>'
            for c in cfg["checks"]
        ])
        st.markdown(
            f'<div class="section-card"><div class="section-title">Before Trading</div>{checks_html}</div>',
            unsafe_allow_html=True,
        )

        # ── Exit check
        exit_html = "".join([
            f'<div style="padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:0.83rem;color:#475569">'
            f'<span style="color:#94a3b8;margin-right:8px">[ ]</span>{exit_item}</div>'
            for exit_item in [
                "Option/spread doubled in value? (stop)",
                "Short strike breached? (ITM)",
                "QQQ confirmed regime change?",
                "50% profit GTC order filled?",
            ]
        ])
        st.markdown(
            f'<div class="section-card"><div class="section-title">Open Position Exit Check</div>{exit_html}</div>',
            unsafe_allow_html=True,
        )

        # ── Portfolio Calculator
        st.markdown("---")
        st.markdown("#### Portfolio Calculator *(optional)*")
        st.caption("Enter account details for exact sizing.")
        pc1, pc2 = st.columns(2)
        with pc1:
            nlv = st.number_input("Account NLV ($)", min_value=1000, value=42000, step=1000)
            strike = st.number_input("Intended Strike ($)", min_value=1.0, value=45.0, step=0.5)
        with pc2:
            existing = st.number_input("Existing Assignment ($)", min_value=0, value=0, step=1000)
            premium = st.number_input("Est. Premium / Contract ($)", min_value=0.01, value=0.58, step=0.01)

        if dep_pct > 0:
            max_assign = nlv * dep_pct / 100
            available = max(0, max_assign - existing)
            max_contracts = int(available / (strike * 100))
            new_assign = max_contracts * strike * 100
            total_assign = existing + new_assign
            total_pct = total_assign / nlv * 100
            prem_total = max_contracts * premium * 100
            buffer_req = max(prem_total * 2, nlv * 0.05)

            # Regime risk check
            regime_ok = total_pct <= dep_pct
            strike_ok = (sz_low is None) or (sz_low <= strike <= sz_high + 2)

            if max_contracts <= 0:
                css = "calc-fail"
                status = "NO ROOM — at or above deployment cap"
                risk_rating = "ELEVATED"
            elif regime_ok and strike_ok:
                css = "calc-pass"
                status = "PASSES ALL SIZING RULES"
                risk_rating = "ACCEPTABLE"
            elif not regime_ok:
                css = "calc-warn"
                status = "NEAR DEPLOYMENT LIMIT"
                risk_rating = "ELEVATED"
            else:
                css = "calc-warn"
                status = "STRIKE OUTSIDE SUGGESTED ZONE"
                risk_rating = "ELEVATED"

            st.markdown(
                f'<div class="{css}">'
                f'<div style="font-weight:700;margin-bottom:8px;font-size:0.85rem">{status}</div>'
                f'<div style="font-size:0.81rem;color:#334155;line-height:1.9">'
                f'Deployment cap ({dep_pct}%): &nbsp;${max_assign:,.0f}<br>'
                f'Existing exposure: &nbsp;${existing:,.0f}<br>'
                f'Available room: &nbsp;${available:,.0f}<br>'
                f'<strong>Max contracts: &nbsp;{max_contracts}</strong><br>'
                f'Post-trade utilization: &nbsp;{total_pct:.1f}% of NLV<br>'
                f'Cash buffer needed: &nbsp;${buffer_req:,.0f}<br>'
                f'Post-trade regime risk: &nbsp;<strong>{risk_rating}</strong>'
                + (f'<br><span style="color:#9a3412">Strike ${strike:.1f} is outside suggested zone '
                   f'${sz_low:.1f}–${sz_high:.1f}</span>' if sz_low and not strike_ok else "")
                + "</div></div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("No bullish CSP deployment in current regime. Inverse spread collateral = spread width x contracts.")

        # ── Copy for AI
        st.markdown("---")
        st.markdown("#### Copy for AI")
        st.caption("Select all and paste directly into Claude or Compa.")
        summary = build_summary(
            regime, confidence, sig_label, qqq, sma, pct, vix, vix_sym,
            vix_reliable, rsi, cfg, dep_pct, dep_note, breadth_val,
            gamma_above, gamma_flip_val, put_wall_val, call_wall_val,
            tqqq_price, sz_low, sz_high, et_now,
        )
        st.text_area("", value=summary, height=400, label_visibility="collapsed")
        st.success("Report generated at " + et_now.strftime("%I:%M %p ET"))

st.divider()
st.caption("V10 Playbook - For personal use only - Not financial advice")