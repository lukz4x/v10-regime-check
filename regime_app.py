import streamlit as st
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
.regime-card {border-radius:14px;padding:20px;margin-bottom:16px;border:2px solid}
.section-card {
    background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;
    padding:16px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,0.05)
}
.section-title {
    font-size:0.7rem;font-weight:700;letter-spacing:0.12em;
    text-transform:uppercase;color:#64748b;margin-bottom:10px
}
.row {
    display:flex;justify-content:space-between;align-items:center;
    padding:6px 0;border-bottom:1px solid #f1f5f9
}
.row:last-child {border-bottom:none}
.row-label {color:#64748b;font-size:0.85rem}
.row-value {color:#1e293b;font-size:0.85rem;font-weight:600;text-align:right}
.pill {
    display:inline-block;padding:2px 10px;border-radius:99px;
    font-size:0.75rem;font-weight:700
}
.pill-green {background:#dcfce7;color:#166534}
.pill-red {background:#fee2e2;color:#991b1b}
.pill-yellow {background:#fef9c3;color:#854d0e}
.pill-gray {background:#f1f5f9;color:#475569}
.metric-grid {display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:0}
.metric-box {
    background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px
}
.metric-label {color:#64748b;font-size:0.7rem;letter-spacing:0.08em;text-transform:uppercase}
.metric-value {color:#0f172a;font-size:1.1rem;font-weight:700;font-family:monospace}
.calc-result {
    background:#f0fdf4;border:1px solid #86efac;border-radius:8px;
    padding:10px 12px;margin-top:6px
}
.calc-warn {
    background:#fef9c3;border:1px solid #fde047;border-radius:8px;
    padding:10px 12px;margin-top:6px
}
.calc-fail {
    background:#fee2e2;border:1px solid #fca5a5;border-radius:8px;
    padding:10px 12px;margin-top:6px
}
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


def determine_regime(qqq, sma200, vix):
    above = qqq > sma200
    pct = (qqq - sma200) / sma200 * 100
    if vix > 40:
        return "CAPITULATION", pct
    if not above:
        pct_below = (sma200 - qqq) / sma200 * 100
        if pct_below <= 5 and 20 <= vix <= 28:
            return "NO MAN'S LAND", pct
        return "STRESS", pct
    if vix < 20:
        return "TREND", pct
    return "TENSION", pct


# ── Regime definitions — percentage-based, universal ─────────────────────────
REGIMES = {
    "TREND": {
        "color": "#16a34a", "bg": "#f0fdf4", "border": "#86efac",
        "bullish_csp": True, "inverse_spreads": False, "wildcard": False,
        "deployment_cap": 65, "dte": "7 DTE", "delta": "0.20-0.30",
        "instrument": "TQQQ cash-secured puts",
        "engines": {"Trend CSP": True, "Tension CSP": False, "Stress/Inverse": False, "Wildcard": False},
        "action": "Full CSP engine active. Set GTC profit-take at 50% immediately after fill.",
        "checks": [
            "Check % stocks above 200MA (StockCharts $NAA200R)",
            "> 55% = full deployment cap",
            "40-55% = reduce to Tension deployment cap (45%)",
            "< 40% = reduce to Tension deployment cap (45%)",
        ],
        "cash_buffer": "MAX(2x total premium received, 5% of NLV)",
    },
    "TENSION": {
        "color": "#d97706", "bg": "#fffbeb", "border": "#fcd34d",
        "bullish_csp": True, "inverse_spreads": False, "wildcard": False,
        "deployment_cap": 45, "dte": "14-21 DTE", "delta": "0.15-0.20",
        "instrument": "TQQQ cash-secured puts (further OTM)",
        "engines": {"Trend CSP": False, "Tension CSP": True, "Stress/Inverse": False, "Wildcard": False},
        "action": "Reduced engine. Wider DTE for buffer. Monitor 200MA daily.",
        "checks": [
            "Watch for QQQ reclaiming or losing 200MA",
            "VIX rising toward 28+ = reduce deployment further",
            "Day 0 rule: if transitioning from Stress, observe only today",
        ],
        "cash_buffer": "MAX(2x total premium received, 5% of NLV)",
    },
    "NO MAN'S LAND": {
        "color": "#d97706", "bg": "#fffbeb", "border": "#fcd34d",
        "bullish_csp": False, "inverse_spreads": True, "wildcard": False,
        "deployment_cap": 0, "dte": "7-10 DTE", "delta": "0.20-0.30",
        "instrument": "SPXS or SQQQ credit spreads only (if filter passes)",
        "engines": {"Trend CSP": False, "Tension CSP": False, "Stress/Inverse": True, "Wildcard": False},
        "action": "DO NOT TRADE unless spread passes filter. Median 2-day cluster.",
        "checks": [
            "Credit filter: net credit >= 25% of spread width",
            "Short leg >= 5% OTM from current price",
            "If no spread passes filter: stay 100% cash",
        ],
        "cash_buffer": "100% cash unless spread filter passes",
    },
    "STRESS": {
        "color": "#dc2626", "bg": "#fef2f2", "border": "#fca5a5",
        "bullish_csp": False, "inverse_spreads": True, "wildcard": False,
        "deployment_cap": 0, "dte": "7-10 DTE", "delta": "0.20-0.30",
        "instrument": "SPXS spreads (preferred) / SQQQ credit spreads",
        "engines": {"Trend CSP": False, "Tension CSP": False, "Stress/Inverse": True, "Wildcard": False},
        "action": "Inverse spread engine. No new TQQQ/bullish positions.",
        "checks": [
            "Credit filter: net credit >= 25% of spread width",
            "Short leg >= 5% OTM from current price",
            "Max spread collateral: 15% of NLV per position",
            "SQQQ spreads valid at any account size",
        ],
        "cash_buffer": "MAX(2x total premium received, 5% of NLV)",
    },
    "CAPITULATION": {
        "color": "#7c3aed", "bg": "#faf5ff", "border": "#c4b5fd",
        "bullish_csp": False, "inverse_spreads": False, "wildcard": True,
        "deployment_cap": 0, "dte": "30-45 DTE (wildcard only)", "delta": "~0.35",
        "instrument": "No CSPs. Wildcard: TQQQ long calls if all 5 triggers met.",
        "engines": {"Trend CSP": False, "Tension CSP": False, "Stress/Inverse": False, "Wildcard": True},
        "action": "VIX > 40. No CSPs. Wildcard eligible only if all triggers confirmed.",
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

        vix_price = None
        vix_sym = "unknown"
        for sym in ["VIX", "VIXY", "VXX"]:
            try:
                closes, _ = get_closes(sym, 10)
                if closes:
                    vix_price = closes[-1]
                    vix_sym = sym
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
            "error": None,
        }
    except Exception as e:
        return {"error": str(e)}


# ── HTML helpers ──────────────────────────────────────────────────────────────
def pill(text, style="gray"):
    return f'<span class="pill pill-{style}">{text}</span>'

def row(label, value_html):
    return (f'<div class="row"><span class="row-label">{label}</span>'
            f'<span class="row-value">{value_html}</span></div>')

def section(title, rows_html):
    inner = "".join(rows_html)
    return (f'<div class="section-card"><div class="section-title">{title}</div>'
            f'{inner}</div>')


# ── AI summary text ───────────────────────────────────────────────────────────
def build_ai_summary(regime, qqq, sma, pct, vix, vix_sym, rsi, cfg, et_now):
    sign = "+" if pct >= 0 else ""
    above = "ABOVE" if pct >= 0 else "BELOW"
    engines = cfg["engines"]
    on_off = lambda v: "ON" if v else "OFF"
    lines = [
        "=== V10 MORNING REGIME REPORT ===",
        et_now.strftime("%A, %B %d, %Y  %I:%M %p ET"),
        "",
        f"REGIME: {regime}",
        f"Action: {cfg['action']}",
        "",
        "--- MARKET DATA ---",
        f"QQQ:         ${qqq:.2f}  ({data['qqq_date']})",
        f"200-day SMA: ${sma:.2f}",
        f"QQQ vs SMA:  {sign}{pct:.2f}% ({above})",
        f"VIX:         {vix:.1f}  [via {vix_sym}]",
        f"QQQ RSI(14): {rsi}",
        "",
        "--- PLAYBOOK OUTPUT ---",
        f"Bullish CSPs Allowed: {'YES' if cfg['bullish_csp'] else 'NO'}",
        f"Inverse Spreads:      {'ACTIVE' if cfg['inverse_spreads'] else 'OFF'}",
        f"Deployment Cap:       {cfg['deployment_cap']}% of NLV (assignment utilization)",
        f"Cash Buffer Min:      {cfg['cash_buffer']}",
        f"Target DTE:           {cfg['dte']}",
        f"Target Delta:         {cfg['delta']}",
        f"Instrument:           {cfg['instrument']}",
        "",
        "--- EXECUTION STATUS ---",
        f"Trend CSP Engine:  {on_off(engines['Trend CSP'])}",
        f"Tension CSP Engine:{on_off(engines['Tension CSP'])}",
        f"Stress/Inverse:    {on_off(engines['Stress/Inverse'])}",
        f"Wildcard Eligible: {on_off(engines['Wildcard'])}",
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
        "NOTE: Contract count = FLOOR((NLV x Deployment Cap%) / Strike).",
        "Enter NLV and strike in the portfolio calculator for exact sizing.",
    ]
    if vix_sym != "VIX":
        lines.append("NOTE: VIX is estimated. Verify on CBOE if near 18-22 or 38-42.")
    return "\n".join(lines)


# ── UI ────────────────────────────────────────────────────────────────────────
st.markdown("### V10 Morning Regime Check")
et_now = datetime.now(ZoneInfo("America/New_York"))
st.caption(et_now.strftime("%A, %B %d, %Y  -  %I:%M %p ET"))
st.divider()

api_key = st.secrets.get("ALPACA_API_KEY", "")
secret_key = st.secrets.get("ALPACA_SECRET_KEY", "")

if not api_key or not secret_key:
    with st.expander("API Keys", expanded=True):
        api_key = st.text_input("Alpaca API Key ID")
        secret_key = st.text_input("Alpaca Secret Key", type="password")
        st.caption("Add to Streamlit Secrets to avoid entering every time.")

data = None

if st.button("Run Regime Check", type="primary"):
    if not api_key or not secret_key:
        st.error("Enter your Alpaca API keys first.")
    else:
        with st.spinner("Fetching live data..."):
            data = fetch_data(api_key, secret_key)
        st.session_state["last_data"] = data

if "last_data" in st.session_state and st.session_state["last_data"]:
    data = st.session_state["last_data"]

if data:
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

        # ── Regime banner
        st.markdown(
            '<div class="regime-card" style="background:' + cfg["bg"]
            + ';border-color:' + cfg["border"] + '">'
            + '<div style="font-size:0.7rem;font-weight:700;letter-spacing:0.12em;'
            + 'text-transform:uppercase;color:' + cfg["color"] + ';margin-bottom:4px">REGIME</div>'
            + '<div style="font-size:2rem;font-weight:900;color:' + cfg["color"] + '">'
            + regime + "</div>"
            + '<div style="color:#475569;font-size:0.85rem;margin-top:8px">'
            + cfg["action"] + "</div></div>",
            unsafe_allow_html=True,
        )

        # ── Market Data
        sma_color = "#16a34a" if above else "#dc2626"
        vix_color = "#dc2626" if vix > 30 else "#d97706" if vix > 20 else "#16a34a"
        rsi_val = str(rsi) if rsi else "N/A"
        rsi_color = "#dc2626" if rsi and rsi < 30 else "#d97706" if rsi and rsi > 70 else "#0f172a"

        st.markdown(
            '<div class="section-card">'
            + '<div class="section-title">Market Data &nbsp;&nbsp;'
            + '<span style="font-size:0.65rem;color:#94a3b8;font-weight:400">'
            + "QQQ as of " + data["qqq_date"] + "</span></div>"
            + '<div class="metric-grid">'
            + '<div class="metric-box"><div class="metric-label">QQQ</div>'
            + '<div class="metric-value">$' + f"{qqq:.2f}" + "</div></div>"
            + '<div class="metric-box"><div class="metric-label">200-day SMA</div>'
            + '<div class="metric-value">$' + f"{sma:.2f}" + "</div></div>"
            + '<div class="metric-box"><div class="metric-label">vs 200MA</div>'
            + '<div class="metric-value" style="color:' + sma_color + '">'
            + sign + f"{pct:.2f}%" + "</div></div>"
            + '<div class="metric-box"><div class="metric-label">VIX</div>'
            + '<div class="metric-value" style="color:' + vix_color + '">'
            + f"{vix:.1f}"
            + '<span style="font-size:0.65rem;color:#94a3b8;font-weight:400"> via '
            + data["vix_sym"] + "</span></div></div>"
            + '<div class="metric-box"><div class="metric-label">QQQ RSI(14)</div>'
            + '<div class="metric-value" style="color:' + rsi_color + '">'
            + rsi_val + "</div></div>"
            + "</div></div>",
            unsafe_allow_html=True,
        )

        if data["vix_sym"] != "VIX":
            st.caption("VIX is estimated. Verify on CBOE if near boundary (18-22 or 38-42).")

        # ── Playbook Output
        csp_pill = pill("ALLOWED", "green") if cfg["bullish_csp"] else pill("NOT ALLOWED", "red")
        inv_pill = pill("ACTIVE", "green") if cfg["inverse_spreads"] else pill("OFF", "gray")
        wc_pill = pill("ELIGIBLE", "yellow") if cfg["wildcard"] else pill("NOT ELIGIBLE", "gray")
        dep = str(cfg["deployment_cap"]) + "% of NLV" if cfg["deployment_cap"] > 0 else "0% (no new bullish)"

        st.markdown(
            section("Playbook Output", [
                row("Bullish CSPs", csp_pill),
                row("Inverse Spreads", inv_pill),
                row("Wildcard", wc_pill),
                row("Max Assignment Utilization", dep),
                row("Cash Buffer Minimum", cfg["cash_buffer"]),
                row("Target DTE", cfg["dte"]),
                row("Target Delta", cfg["delta"]),
                row("Instrument", cfg["instrument"]),
            ]),
            unsafe_allow_html=True,
        )

        # ── Execution Status
        engines = cfg["engines"]
        def eng_pill(v):
            return pill("ON", "green") if v else pill("OFF", "gray")

        st.markdown(
            section("Execution Status", [
                row("Trend CSP Engine", eng_pill(engines["Trend CSP"])),
                row("Tension CSP Engine", eng_pill(engines["Tension CSP"])),
                row("Stress / Inverse Engine", eng_pill(engines["Stress/Inverse"])),
                row("Wildcard Eligible", eng_pill(engines["Wildcard"])),
            ]),
            unsafe_allow_html=True,
        )

        # ── Before Trading
        checks_html = "".join([
            '<div style="padding:5px 0;border-bottom:1px solid #f1f5f9;font-size:0.85rem;color:#475569">'
            + '<span style="color:' + cfg["color"] + ';margin-right:8px;font-weight:700">&rsaquo;</span>'
            + c + "</div>"
            for c in cfg["checks"]
        ])
        st.markdown(
            '<div class="section-card"><div class="section-title">Before Trading</div>'
            + checks_html + "</div>",
            unsafe_allow_html=True,
        )

        # ── Position Exit Check
        exit_items = [
            "Option/spread doubled in value? (stop)",
            "Short strike breached? (ITM exit)",
            "QQQ confirmed a regime change?",
            "50% profit GTC order filled?",
        ]
        exit_html = "".join([
            '<div style="padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:0.85rem;color:#475569">'
            + '<span style="color:#94a3b8;margin-right:8px">[ ]</span>' + e + "</div>"
            for e in exit_items
        ])
        st.markdown(
            '<div class="section-card"><div class="section-title">Open Position Exit Check</div>'
            + exit_html + "</div>",
            unsafe_allow_html=True,
        )

        # ── Portfolio Calculator
        st.markdown("---")
        st.markdown("#### Portfolio Calculator *(optional)*")
        st.caption("Enter your account details to compute exact position sizing.")

        col1, col2 = st.columns(2)
        with col1:
            nlv = st.number_input("Account NLV ($)", min_value=1000, value=42000, step=1000)
            strike = st.number_input("Intended Strike ($)", min_value=1.0, value=45.0, step=0.5)
        with col2:
            existing_assignment = st.number_input("Existing Assignment ($)", min_value=0, value=0, step=1000)
            premium = st.number_input("Est. Premium per Contract ($)", min_value=0.01, value=0.58, step=0.01)

        if cfg["deployment_cap"] > 0:
            max_assignment = nlv * cfg["deployment_cap"] / 100
            available_assignment = max(0, max_assignment - existing_assignment)
            max_contracts = int(available_assignment / (strike * 100))
            new_assignment = max_contracts * strike * 100
            total_assignment = existing_assignment + new_assignment
            total_pct = total_assignment / nlv * 100
            premium_total = max_contracts * premium * 100
            cash_buffer_required = max(premium_total * 2, nlv * 0.05)
            assignment_utilization = total_pct

            if max_contracts > 0:
                css_class = "calc-result" if total_pct <= cfg["deployment_cap"] else "calc-warn"
                status = "PASSES SIZING RULES" if total_pct <= cfg["deployment_cap"] else "NEAR LIMIT"
            else:
                css_class = "calc-fail"
                status = "NO ROOM — existing exposure at or above cap"

            st.markdown(
                '<div class="' + css_class + '">'
                + '<div style="font-weight:700;margin-bottom:8px;font-size:0.85rem">' + status + "</div>"
                + '<div style="font-size:0.82rem;color:#334155;line-height:1.8">'
                + f"Max assignment cap: ${max_assignment:,.0f} ({cfg['deployment_cap']}% of NLV)<br>"
                + f"Existing exposure:  ${existing_assignment:,.0f}<br>"
                + f"Available room:     ${available_assignment:,.0f}<br>"
                + f"<strong>Max contracts:      {max_contracts}</strong><br>"
                + f"New assignment:     ${new_assignment:,.0f}<br>"
                + f"Total utilization:  {total_pct:.1f}% of NLV<br>"
                + f"Cash buffer needed: ${cash_buffer_required:,.0f}"
                + "</div></div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("No bullish CSP deployment allowed in current regime. Calculator applies to inverse spreads only — collateral = spread width x contracts.")

        # ── Copy for AI
        st.markdown("---")
        st.markdown("#### Copy for AI")
        st.caption("Select all and paste into Claude or Compa.")
        summary = build_ai_summary(regime, qqq, sma, pct, vix, data["vix_sym"], rsi, cfg, et_now)
        st.text_area("", value=summary, height=360, label_visibility="collapsed")

        st.success("Report generated at " + et_now.strftime("%I:%M %p ET"))

st.divider()
st.caption("V10 Playbook - For personal use only - Not financial advice")
