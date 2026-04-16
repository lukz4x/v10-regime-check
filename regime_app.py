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

    if vix > 40 and vix_reliable:
        return "CAPITULATION", pct, "HIGH"
    if not above:
        pct_below = (sma200 - qqq) / sma200 * 100
        if pct_below <= 5 and 20 <= vix <= 28:
            return "NO MAN'S LAND", pct, "MEDIUM"
        return "STRESS", pct, "HIGH"

    if vix_reliable:
        if vix > 40:
            return "CAPITULATION", pct, "HIGH"
        if vix >= 20:
            regime = "TENSION"
        else:
            regime = "TREND"
    else:
        regime = "TENSION"

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
            regime = "TENSION"

    # Breadth 40-55%: moderate zone — does not award bull signal,
    # but also caps confidence at MEDIUM even if other signals are strong
    breadth_moderate = (breadth is not None and 40 <= breadth < 55)

    if bull_signals >= 3 and bear_signals == 0 and not breadth_moderate:
        confidence = "HIGH"
    elif bear_signals >= 1:
        if vix_reliable and vix < 20 and pct > 3:
            confidence = "MODERATE"
        else:
            confidence = "LOW"
    elif breadth_moderate and regime == "TREND":
        # Strong tape but breadth not yet confirmed — MEDIUM, not HIGH
        confidence = "MEDIUM"
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
    if regime == "TENSION" and confidence == "MODERATE":
        return "TREND-LIKE", "yellow", "Breadth-constrained Trend. Deploy at 40-45% cap, 7 DTE."
    if regime == "TENSION" and confidence == "LOW":
        return "CAUTION", "orange", "Fragile Tension. Reduce deployment, stay OTM."
    if regime == "TENSION":
        return "CAUTION", "yellow", "Tension. Mid-sized deployment, wider DTE."
    return "HOLD", "gray", "Assess conditions before trading."

def suggested_deployment(regime, confidence, breadth):
    if regime == "TREND":
        if breadth is None or breadth >= 55:
            return 65, "Full Trend cap — breadth confirmed >55%"
        if breadth >= 50:
            return 55, "Improving — breadth 50-55%, near full but not yet confirmed"
        if breadth >= 40:
            return 45, "Moderate — breadth 40-50%, Tension-sized until >55%"
        return 40, "Weak breadth (<40%) — stay conservative"
    if regime == "TENSION":
        if breadth is not None and breadth < 40:
            return 40, "Weak breadth — reduced but within Tension cap"
        return 45, "Standard Tension cap — watch breadth for Trend upgrade"
    return 0, "No bullish deployment allowed"

def strike_zone(tqqq_price, put_wall, gamma_flip, rsi=None, gamma_at_flip=False):
    """
    AUTHORITATIVE strike zone — called once, used everywhere.
    Primary: sell below put wall (put wall = upper anchor, dealer support floor).
    Fallback: 3-5% OTM from current price if no put wall available.

    RSI override: When daily RSI > 80, step zone down one tier (more OTM)
    to avoid top-end strikes into momentum extension.
    Gamma flip proximity: When price is within 0.5% of flip, step down further.
    """
    if tqqq_price is None:
        return None, None

    # Determine if we should step down the zone
    rsi_extended = rsi is not None and rsi > 80
    flip_fragile  = gamma_at_flip  # already computed upstream

    if put_wall and put_wall < tqqq_price:
        sz_high = put_wall
        sz_low  = round(put_wall - 1.5, 1)
        # RSI > 80: drop the zone by one full tier (~1.5 pts)
        if rsi_extended:
            sz_high = sz_low
            sz_low  = round(sz_high - 1.5, 1)
        # Price sitting on gamma flip: drop a half tier on top of any RSI shift
        elif flip_fragile:
            sz_high = round(sz_high - 0.5, 1)
            sz_low  = round(sz_low  - 0.5, 1)
        return sz_low, sz_high

    # Fallback — price-based
    base_otm = 0.95 if rsi_extended else 0.97
    sz_high = round(tqqq_price * base_otm, 1)
    sz_low  = round(tqqq_price * (base_otm - 0.02), 1)
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
        "max_cap": 45, "dte": "7 DTE", "delta": "0.15-0.20",
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

        tqqq_price = None
        try:
            tqqq_closes, _ = get_closes("TQQQ", 5)
            if tqqq_closes:
                tqqq_price = tqqq_closes[-1]
        except Exception:
            pass

        vix_price = None
        vix_sym = None
        vix_reliable = False
        try:
            import yfinance as yf
            vix_ticker = yf.Ticker("^VIX")
            vix_hist = vix_ticker.history(period="2d")
            if not vix_hist.empty:
                vix_price = round(float(vix_hist["Close"].iloc[-1]), 2)
                vix_sym = "^VIX"
                vix_reliable = True
        except Exception:
            pass

        if vix_price is None:
            for sym in ["VIXY", "VXX"]:
                try:
                    closes, _ = get_closes(sym, 10)
                    if closes:
                        vix_price = closes[-1]
                        vix_sym = sym
                        vix_reliable = False
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


@st.cache_data(ttl=180)
def fetch_option_chains(api_key, secret_key, tqqq_price):
    """
    Fetch put chains for next two Fridays.
    Also fetch calls for gamma/wall calculation.
    Returns: {chains, put_wall, call_wall, gamma_flip, error}
    """
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionChainRequest
        from alpaca.trading.enums import ContractType
        from datetime import date, timedelta

        client = OptionHistoricalDataClient(api_key, secret_key)

        today = date.today()
        days_ahead = (4 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_friday = today + timedelta(days=days_ahead)
        friday_after = next_friday + timedelta(days=7)

        gamma_by_strike = {}
        put_oi_by_strike = {}
        call_oi_by_strike = {}
        chains = {}

        for exp in [next_friday, friday_after]:
            label = f"{exp.strftime('%b %d')} ({(exp - today).days} DTE)"
            puts = []
            try:
                req = OptionChainRequest(
                    underlying_symbol="TQQQ",
                    type=ContractType.PUT,
                    expiration_date=str(exp),
                    strike_price_gte=tqqq_price * 0.82,
                    strike_price_lte=tqqq_price * 1.05,
                )
                chain = client.get_option_chain(req)
                for symbol, snap in chain.items():
                    try:
                        strike_val = int(symbol[-8:]) / 1000
                        bid = snap.latest_quote.bid_price if snap.latest_quote else 0
                        ask = snap.latest_quote.ask_price if snap.latest_quote else 0
                        mid = round((bid + ask) / 2, 2)
                        delta = None
                        gamma = 0
                        oi = 0
                        if snap.greeks:
                            delta = round(abs(snap.greeks.delta), 3)
                            gamma = snap.greeks.gamma or 0
                        if snap.latest_trade:
                            oi = snap.latest_trade.size or 0
                        if bid > 0.05:
                            puts.append({
                                "strike": strike_val, "bid": round(bid, 2),
                                "ask": round(ask, 2), "mid": mid, "delta": delta,
                            })
                        if strike_val not in gamma_by_strike:
                            gamma_by_strike[strike_val] = 0
                        gamma_by_strike[strike_val] -= (gamma or 0) * max(oi, 1) * 100
                        put_oi_by_strike[strike_val] = put_oi_by_strike.get(strike_val, 0) + max(oi, 1)
                    except Exception:
                        continue
                puts.sort(key=lambda x: x["strike"], reverse=True)
                chains[label] = puts
            except Exception as ex:
                chains[label] = {"error": str(ex)}

            # Fetch calls for gamma flip / call wall
            try:
                req_c = OptionChainRequest(
                    underlying_symbol="TQQQ",
                    type=ContractType.CALL,
                    expiration_date=str(exp),
                    strike_price_gte=tqqq_price * 0.82,
                    strike_price_lte=tqqq_price * 1.05,
                )
                chain_c = client.get_option_chain(req_c)
                for symbol, snap in chain_c.items():
                    try:
                        strike_val = int(symbol[-8:]) / 1000
                        gamma = 0
                        oi = 0
                        if snap.greeks:
                            gamma = snap.greeks.gamma or 0
                        if snap.latest_trade:
                            oi = snap.latest_trade.size or 0
                        if strike_val not in gamma_by_strike:
                            gamma_by_strike[strike_val] = 0
                        gamma_by_strike[strike_val] += gamma * max(oi, 1) * 100
                        call_oi_by_strike[strike_val] = call_oi_by_strike.get(strike_val, 0) + max(oi, 1)
                    except Exception:
                        continue
            except Exception:
                pass

        # ── Calculate walls and gamma flip ───────────────────────────────────
        put_wall = None
        call_wall = None
        gamma_flip = None

        # Put wall = highest OI among strikes BELOW current price
        # Call wall = highest OI among strikes ABOVE current price
        # Without this filter, covered-call OI from prior assignments
        # (e.g. $47-48 strikes from March stress episode) can dominate
        # the full chain and produce a nonsensical call wall below spot.
        if put_oi_by_strike and tqqq_price:
            below = {s: v for s, v in put_oi_by_strike.items() if s < tqqq_price}
            if below:
                put_wall = round(max(below, key=below.get), 1)
        if call_oi_by_strike and tqqq_price:
            above = {s: v for s, v in call_oi_by_strike.items() if s > tqqq_price}
            if above:
                call_wall = round(max(above, key=above.get), 1)

        # Gamma flip: highest strike below price where net gamma crosses from negative to positive
        if gamma_by_strike and tqqq_price:
            sorted_strikes = sorted(gamma_by_strike.keys())
            below_price = [s for s in sorted_strikes if s <= tqqq_price]
            for i in range(len(below_price) - 1, 0, -1):
                s = below_price[i]
                s_prev = below_price[i - 1]
                if gamma_by_strike.get(s, 0) > 0 and gamma_by_strike.get(s_prev, 0) <= 0:
                    gamma_flip = round((s + s_prev) / 2, 2)
                    break
                elif gamma_by_strike.get(s, 0) <= 0:
                    gamma_flip = round(s, 2)
                    break

        return {
            "chains": chains,
            "put_wall": put_wall,
            "call_wall": call_wall,
            "gamma_flip": gamma_flip,
            "error": None,
        }
    except Exception as e:
        return {"error": str(e), "chains": {}}


# ── NDX100 components (as of Q1 2026) ────────────────────────────────────────
NDX100 = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","TSLA","AVGO","COST",
    "NFLX","TMUS","ASML","AMD","PEP","LIN","ADBE","QCOM","CSCO","TXN",
    "INTU","ISRG","AMGN","BKNG","AMAT","MU","HON","ADI","VRTX","PANW",
    "SBUX","LRCX","GILD","REGN","MELI","KDP","CTAS","MDLZ","INTC","CDNS",
    "KLAC","SNPS","PYPL","CRWD","ORLY","NXPI","CEG","ABNB","WDAY","MNST",
    "MRVL","FTNT","ADSK","PCAR","ROST","FANG","DXCM","ODFL","CHTR","CPRT",
    "IDXX","FAST","PAYX","VRSK","BIIB","CSGP","EA","GEHC","EXC","ZS",
    "DDOG","MRNA","BKR","CCEP","TTWO","ON","XEL","TEAM","ANSS","GFS",
    "DLTR","WBA","PDD","CDW","ILMN","SIRI","ORCL","AZN","ARM","DASH",
    "PLTR","MSTR","APP","TTD","HOOD","SMCI","RBLX","ANET","HUBS","ALGN",
]

@st.cache_data(ttl=600)
def fetch_breadth(api_key, secret_key):
    """
    Compute % of NDX100 stocks above their 200-day SMA.
    Single bulk bar request — works on both paper and live Alpaca keys.
    """
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(api_key, secret_key)
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        end = datetime.now(et)
        start = end - timedelta(days=320)

        req = StockBarsRequest(
            symbol_or_symbols=NDX100,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed="iex",
        )
        bars = client.get_stock_bars(req).df

        # Normalize MultiIndex — always reset to (symbol, timestamp) structure
        import pandas as pd
        df = bars.df if hasattr(bars, "df") else bars
        if not isinstance(df.index, pd.MultiIndex):
            return None, "Unexpected bar response format"

        above = 0
        total = 0
        missing = []
        for sym in NDX100:
            try:
                if sym not in df.index.get_level_values(0):
                    missing.append(sym)
                    continue
                sym_df = df.loc[sym].sort_index()
                closes = sym_df["close"].values
                if len(closes) < 200:
                    missing.append(sym)
                    continue
                sma200 = float(closes[-200:].mean())
                current = float(closes[-1])
                total += 1
                if current > sma200:
                    above += 1
            except Exception as sym_err:
                missing.append(sym)
                continue

        if total == 0:
            return None, f"No stocks computed (skipped: {len(missing)})"

        pct = round(above / total * 100)
        note = f"{above}/{total} NDX stocks above 200MA"
        if missing:
            note += f" ({len(missing)} skipped)"
        return pct, note

    except Exception as e:
        return None, str(e)

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
                  sz_low, sz_high, et_now, chain_data=None):
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
        f"RSI NOTE:         {'RSI >' + str(round(rsi,1)) + ' — EXTENDED: avoid top-end strikes, prefer lower half of zone, consider waiting for pullback' if rsi and rsi > 80 else ('RSI >' + str(round(rsi,1)) + ' — elevated, monitor' if rsi and rsi > 70 else '')}" if rsi else "",
        "",
        "— MARKET DATA —",
        f"QQQ:         ${qqq:.2f}",
        f"200-day SMA: ${sma:.2f}",
        f"QQQ vs SMA:  {sign}{pct:.2f}% ({above})",
        f"VIX:         {vix:.1f} via {vix_sym}"
        + (" [CONFIRMED]" if vix_reliable else " [LOW CONFIDENCE - verify on CBOE]"),
        f"QQQ RSI(14): {rsi if rsi else 'N/A'}",
        "",
        "— MARKET STRUCTURE —",
        f"TQQQ Price:       ${tqqq_price:.2f}" if tqqq_price else "TQQQ Price:       N/A",
        f"Gamma Regime:     {'POSITIVE (above flip)' if gamma_above else 'NEGATIVE (below flip)' if gamma_above is not None else 'Not provided'}" + (" ⚠️ PRICE AT FLIP — cushion minimal" if (gamma_flip and tqqq_price and abs(tqqq_price - gamma_flip) / tqqq_price < 0.01) else ""),
        f"Gamma Flip:       {gamma_flip if gamma_flip else 'Not provided'}",
        f"Put Wall:         {put_wall if put_wall else 'Not provided'}",
        f"Call Wall:        {call_wall if call_wall else 'Not provided'}",
        f"Wall Status:      {wall_warning if wall_warning else 'OK'}",
        f"Breadth (>200MA): {str(breadth) + '%' if breadth is not None else 'Not provided'}"
        + (" [WEAK]" if breadth is not None and breadth < 40 else
           " [MODERATE]" if breadth is not None and breadth < 55 else
           " [STRONG]" if breadth is not None else ""),
        "",
        "— PLAYBOOK OUTPUT —",
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
        lines.append(f"Strike Zone:      ${sz_low:.1f} – ${sz_high:.1f} (below put wall)")
    if breadth is not None and breadth < 55 and regime in ["TENSION", "TREND"] and dep_pct < 65:
        lines.append(f"Breadth Override:  Active — full Trend not authorized until breadth > 55%")
        lines.append(f"                   Current breadth {breadth}% must reach 55%+ to unlock 65% deployment")
    lines += [
        "",
        "— ENGINES —",
        f"Trend CSP:        {on_off(e['Trend CSP'])}",
        f"Tension CSP:      {on_off(e['Tension CSP'])}",
        f"Stress/Inverse:   {on_off(e['Stress/Inverse'])}",
        f"Wildcard:         {on_off(e['Wildcard'])}",
        "",
        "— BEFORE TRADING —",
    ]
    for c in cfg["checks"]:
        lines.append(f"  - {c}")
    lines += [
        "",
        "— OPEN POSITION EXIT CHECK —",
        "  [ ] Option/spread doubled in value? (stop)",
        "  [ ] Short strike breached? (ITM)",
        "  [ ] QQQ confirmed regime change?",
        "  [ ] 50% profit GTC order filled?",
        "",
        "NOTE: Contracts = FLOOR((NLV x Deployment%) / Strike).",
    ]
    if not vix_reliable:
        lines.append("NOTE: VIX is estimated — DO NOT use for threshold decisions. Verify on CBOE.")

    # ── Append put chains if available ────────────────────────────────────────
    if chain_data and not chain_data.get("error") and chain_data.get("chains"):
        lines.append("")
        lines.append("— PUT CHAINS —")
        for exp_label, puts in chain_data.get("chains", {}).items():
            if isinstance(puts, dict) and "error" in puts:
                lines.append(f"{exp_label}: error loading chain")
                continue
            if not puts:
                lines.append(f"{exp_label}: no data")
                continue
            liquid = [p for p in puts if p.get("bid", 0) > 0.05][:14]
            if not liquid:
                lines.append(f"{exp_label}: no liquid strikes")
                continue
            lines.append(f"{exp_label}:")
            lines.append(f"  {'Strike':<8} {'Bid':>6} {'Ask':>6} {'Mid':>6} {'Delta':>7}")
            lines.append(f"  {'-'*38}")
            for p in liquid:
                in_zone = sz_low and sz_high and sz_low <= p["strike"] <= sz_high
                marker = "  ✓" if in_zone else ""
                delta_s = f"{p['delta']:.3f}" if p.get("delta") else "  —  "
                lines.append(
                    f"  ${p['strike']:<7.1f} ${p['bid']:>5.2f} ${p['ask']:>5.2f}"
                    f" ${p['mid']:>5.2f} {delta_s:>7}{marker}"
                )
            lines.append("")

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

st.markdown("#### Manual Inputs")
st.caption("Breadth auto-computed from NDX100 vs 200MA on Run. Override below only if needed. Gamma/walls and VIX auto-fetched.")

col1, col2 = st.columns(2)
with col1:
    breadth_raw = st.number_input(
        "% Stocks Above 200MA", min_value=0, max_value=100,
        value=None, step=1, placeholder="e.g. 39"
    )
    vix_manual_raw = st.number_input(
        "VIX override (optional)", min_value=0.0,
        value=None, step=0.1, placeholder="Auto-fetched",
        help="Leave blank to use auto-fetched VIX. Enter manually only to override."
    )
with col2:
    gamma_flip_raw = st.number_input(
        "Gamma Flip override", min_value=0.0,
        value=None, step=0.5, placeholder="Auto-calculated",
        help="Leave blank — calculated from option chain. Override if you have Barchart value."
    )
    put_wall_raw = st.number_input(
        "Put Wall override", min_value=0.0,
        value=None, step=0.5, placeholder="Auto-calculated",
        help="Leave blank — calculated from option chain OI."
    )

# Manual override only here — auto_breadth resolved inside display block
breadth_val_manual = int(breadth_raw) if breadth_raw and breadth_raw > 0 else None

gamma_flip_manual = float(gamma_flip_raw) if gamma_flip_raw and gamma_flip_raw > 0 else None
put_wall_manual = float(put_wall_raw) if put_wall_raw and put_wall_raw > 0 else None
vix_manual_val = float(vix_manual_raw) if vix_manual_raw and vix_manual_raw > 0 else None

# ── Button: fetch BOTH market data AND chains together ────────────────────────
if st.button("Run Regime Check", type="primary"):
    if not api_key or not secret_key:
        st.error("Enter your Alpaca API keys first.")
    else:
        with st.spinner("Fetching market data…"):
            fetched = fetch_data(api_key, secret_key)
            st.session_state["last_data"] = fetched

        # Fetch chains immediately so auto-values are ready before any rendering
        if fetched and not fetched.get("error") and fetched.get("tqqq_price"):
            with st.spinner("Loading option chains…"):
                chain_result = fetch_option_chains(api_key, secret_key, fetched["tqqq_price"])
                st.session_state["chain_data"] = chain_result

        with st.spinner("Computing breadth (NDX100 vs 200MA)…"):
            auto_breadth, breadth_note = fetch_breadth(api_key, secret_key)
            st.session_state["auto_breadth"] = auto_breadth
            st.session_state["breadth_note"] = breadth_note

# ── Display ───────────────────────────────────────────────────────────────────
data = st.session_state.get("last_data")

if data:
    if data.get("error"):
        st.error("Error: " + data["error"])
    else:
        qqq = data["qqq_price"]
        sma = data["sma200"]
        rsi = data["rsi14"]
        tqqq_price = data.get("tqqq_price")

        # Resolve breadth — read session_state HERE so button-press fetch is available
        auto_breadth = st.session_state.get("auto_breadth")
        breadth_note_auto = st.session_state.get("breadth_note", "not yet fetched")
        breadth_source = "not available"
        if breadth_val_manual is not None:
            breadth_val = breadth_val_manual
            breadth_source = "manual override"
        elif auto_breadth is not None:
            breadth_val = auto_breadth
            breadth_source = f"auto (NDX100): {breadth_note_auto}"
        else:
            breadth_val = None

        # VIX
        if vix_manual_val:
            vix = vix_manual_val
            vix_sym = "manual"
            vix_reliable = True
        else:
            vix = data["vix"]
            vix_sym = data["vix_sym"]
            vix_reliable = data["vix_reliable"]

        # ── RESOLVE GAMMA/WALLS BEFORE ANY RENDERING ──────────────────────────
        # Priority: manual override > auto from chains > None
        chain_data = st.session_state.get("chain_data")
        chain_put_wall  = chain_data.get("put_wall")  if chain_data and not chain_data.get("error") else None
        chain_call_wall = chain_data.get("call_wall") if chain_data and not chain_data.get("error") else None
        chain_gamma_flip= chain_data.get("gamma_flip")if chain_data and not chain_data.get("error") else None

        gamma_flip_val = gamma_flip_manual if gamma_flip_manual else chain_gamma_flip
        put_wall_val   = put_wall_manual   if put_wall_manual   else chain_put_wall
        call_wall_val  = chain_call_wall   # always auto, no manual override needed

        # ── Wall structure validation ─────────────────────────────────────────
        # Flag conditions where the auto-calculated walls are structurally invalid.
        # These can occur when heavy covered-call OI from prior assignments
        # dominates the chain and produces inverted or nonsensical wall values.
        wall_warning = None
        wall_exceeded = None   # price has blown through a wall
        if tqqq_price and put_wall_val and call_wall_val:
            if call_wall_val < put_wall_val:
                wall_warning = "⚠️ STRUCTURE INVALID: Call wall below put wall — OI likely contaminated by prior-assignment covered calls. Enter walls manually from Barchart."
            elif call_wall_val < tqqq_price:
                wall_exceeded = f"⚠️ PRICE ABOVE CALL WALL (${call_wall_val:.1f}): Wall exceeded — not a valid anchor. Strike zone is price-based fallback only."
            elif put_wall_val > tqqq_price:
                wall_warning = "⚠️ STRUCTURE INVALID: Put wall above current price — data error. Enter put wall manually."
        elif tqqq_price and put_wall_val and put_wall_val > tqqq_price:
            wall_warning = "⚠️ STRUCTURE INVALID: Put wall above current price — data error. Enter put wall manually."

        # Gamma regime
        gamma_above = None
        gamma_at_flip = False
        if gamma_flip_val and tqqq_price:
            gamma_above = tqqq_price > gamma_flip_val
            # Within 1% of flip = mean-reversion cushion is minimal
            gamma_at_flip = abs(tqqq_price - gamma_flip_val) / tqqq_price < 0.01

        # ── SINGLE strike zone calculation — used everywhere below ─────────────
        sz_low, sz_high = strike_zone(tqqq_price, put_wall_val, gamma_flip_val, rsi=rsi, gamma_at_flip=gamma_at_flip)

        # Run regime engine
        regime, pct, confidence = determine_regime(
            qqq, sma, vix, vix_reliable, breadth_val, gamma_above
        )
        cfg = REGIMES[regime]
        sig_label, sig_color, sig_note = execution_signal(regime, confidence, vix_reliable)
        dep_pct, dep_note = suggested_deployment(regime, confidence, breadth_val)
        above = qqq > sma
        sign = "+" if pct >= 0 else ""

        # ── Execution signal bar
        sig_colors = {
            "green":  ("#166534", "#dcfce7", "#86efac"),
            "yellow": ("#854d0e", "#fef9c3", "#fde047"),
            "orange": ("#9a3412", "#fff7ed", "#fed7aa"),
            "red":    ("#991b1b", "#fee2e2", "#fca5a5"),
            "gray":   ("#374151", "#f9fafb", "#e5e7eb"),
        }
        sc = sig_colors.get(sig_color, sig_colors["gray"])
        icon = "🟢" if sig_color == "green" else "🟡" if sig_color == "yellow" else "🟠" if sig_color == "orange" else "🔴"
        st.markdown(
            f'<div class="signal-bar" style="background:{sc[1]};border:1.5px solid {sc[2]}">'
            f'<div style="font-size:1.4rem">{icon}</div>'
            f'<div><div style="font-weight:800;color:{sc[0]};font-size:0.85rem">EXECUTION SIGNAL: {sig_label}</div>'
            f'<div style="color:{sc[0]};font-size:0.78rem;margin-top:2px">{sig_note}</div></div></div>',
            unsafe_allow_html=True,
        )

        # ── Regime card
        conf_pill_style = "green" if confidence == "HIGH" else "yellow" if confidence in ["MEDIUM", "MODERATE"] else "orange"
        breadth_override_html = ""
        if breadth_val is not None and breadth_val < 55 and regime in ["TENSION", "TREND"] and dep_pct < 65:
            breadth_override_html = (
                f'<div style="color:#d97706;font-size:0.78rem;margin-top:6px">'
                f'Breadth override active: full Trend not authorized until breadth &gt; 50% '
                f'(current: {breadth_val}%)</div>'
            )
        st.markdown(
            f'<div class="regime-card" style="background:{cfg["bg"]};border-color:{cfg["border"]}">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">'
            f'<div style="font-size:0.68rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:{cfg["color"]}">REGIME</div>'
            f'{pill(confidence + " CONFIDENCE", conf_pill_style)}</div>'
            f'<div style="font-size:2rem;font-weight:900;color:{cfg["color"]};margin-bottom:8px">{regime}</div>'
            f'<div style="color:#475569;font-size:0.83rem">{sig_note}</div>'
            f'{breadth_override_html}</div>',
            unsafe_allow_html=True,
        )

        if not vix_reliable:
            st.markdown(
                f'<div class="vix-warn">⚠️ VIX showing <strong>{vix:.1f}</strong> via {vix_sym} '
                f'(ETF proxy — LOW CONFIDENCE). Enter real VIX from CBOE in manual override above.</div>',
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
            f'<div class="section-card"><div class="section-title">Market Data '
            f'<span style="font-weight:400">· QQQ as of {data["qqq_date"]}</span></div>'
            f'{metric_html}</div>',
            unsafe_allow_html=True,
        )

        # ── Market Structure — now uses resolved values (auto OR manual override)
        struct_rows = []
        # Show wall structure warnings at the top of the section if invalid
        if wall_warning:
            struct_rows.append(row_html("⚠️ Wall Data", f"<span style='color:#dc2626;font-weight:700;font-size:0.85rem'>{wall_warning}</span>"))
        if wall_exceeded:
            struct_rows.append(row_html("⚠️ Wall Exceeded", f"<span style='color:#b45309;font-weight:700;font-size:0.85rem'>{wall_exceeded}</span>"))
        if tqqq_price:
            struct_rows.append(row_html("TQQQ vs Gamma Flip",
                pill("ABOVE — positive gamma", "green") if gamma_above is True
                else pill("BELOW — negative gamma", "red") if gamma_above is False
                else pill("Enter gamma flip or wait for chains", "gray")))
        if gamma_flip_val:
            src = "(manual)" if gamma_flip_manual else "(auto from chain)"
            flip_warning = " <span style='color:#f59e0b;font-size:0.75rem;font-weight:700'>⚠️ Price at flip — cushion minimal</span>" if gamma_at_flip else ""
            struct_rows.append(row_html("Gamma Flip", f"${gamma_flip_val:.2f} <span style='color:#94a3b8;font-size:0.75rem'>{src}</span>{flip_warning}"))
        if put_wall_val:
            src = "(manual)" if put_wall_manual else "(auto from chain)"
            struct_rows.append(row_html("Put Wall", f"${put_wall_val:.1f} <span style='color:#94a3b8;font-size:0.75rem'>{src}</span>"))
        if call_wall_val:
            struct_rows.append(row_html("Call Wall", f"${call_wall_val:.1f} <span style='color:#94a3b8;font-size:0.75rem'>(auto from chain)</span>"))
        if breadth_val is not None:
            b_style = "green" if breadth_val >= 55 else "yellow" if breadth_val >= 40 else "red"
            b_label = "STRONG" if breadth_val >= 55 else "MODERATE" if breadth_val >= 40 else "WEAK"
            src_tag = f'<span style="color:#94a3b8;font-size:0.72rem;margin-left:6px">({breadth_source})</span>'
            struct_rows.append(row_html("Breadth (% > 200MA)",
                f'{pill(b_label, b_style)} <span style="margin-left:6px">{breadth_val}%</span>{src_tag}'))
        else:
            err_note = st.session_state.get("breadth_note", "run check to fetch")
            struct_rows.append(row_html("Breadth (% > 200MA)",
                f'<span style="color:#dc2626;font-size:0.78rem">Failed: {err_note}</span>'))
        if sz_low and sz_high:
            struct_rows.append(row_html("Suggested Strike Zone",
                f'<span style="color:#166534;font-weight:700">${sz_low:.1f} – ${sz_high:.1f}</span>'
                + (' <span style="color:#94a3b8;font-size:0.75rem">(below put wall)</span>' if put_wall_val else ' <span style="color:#94a3b8;font-size:0.75rem">(price-based fallback)</span>')))
        # RSI execution override note — shown in playbook section, not regime
        rsi_exec_note = None
        if rsi:
            if rsi > 80:
                rsi_exec_note = (f"⚠️ RSI {rsi:.1f} — EXTENDED: avoid top-end strikes, "
                                  f"prefer lower half of zone, "
                                  f"consider waiting for pullback before entry")
            elif rsi > 70:
                rsi_exec_note = f"RSI {rsi:.1f} — Elevated. Monitor momentum before adding size."
        st.markdown(section_html("Market Structure", struct_rows), unsafe_allow_html=True)

        # ── Playbook Output
        csp_p = pill("ALLOWED", "green") if cfg["bullish_csp"] else pill("NOT ALLOWED", "red")
        inv_p = pill("ACTIVE", "green") if cfg["inverse"] else pill("OFF", "gray")
        wc_p  = pill("ELIGIBLE", "yellow") if cfg["wildcard"] else pill("NOT ELIGIBLE", "gray")
        dep_disp = f"{dep_pct}% of NLV" if dep_pct > 0 else pill("0% — no bullish deployment", "red")
        dep_note_disp = f'<span style="color:#64748b;font-size:0.78rem">{dep_note}</span>'
        # Build RSI execution row if note exists
        rsi_exec_rows = []
        if rsi_exec_note:
            rsi_color = "#dc2626" if rsi and rsi > 80 else "#d97706"
            rsi_exec_rows.append(row_html(
                "RSI Entry Signal",
                f"<span style='color:{rsi_color};font-weight:700;font-size:0.83rem'>{rsi_exec_note}</span>"
            ))

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
        ] + rsi_exec_rows), unsafe_allow_html=True)

        # ── Engine Status
        e = cfg["engines"]
        eng_p = lambda v: pill("ON", "green") if v else pill("OFF", "gray")
        st.markdown(section_html("Engine Status", [
            row_html("Trend CSP", eng_p(e["Trend CSP"])),
            row_html("Tension CSP", eng_p(e["Tension CSP"])),
            row_html("Stress / Inverse", eng_p(e["Stress/Inverse"])),
            row_html("Wildcard", eng_p(e["Wildcard"])),
        ]), unsafe_allow_html=True)

        # ── Before Trading
        checks_html = "".join([
            f'<div style="padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:0.83rem;color:#475569">'
            f'<span style="color:{cfg["color"]};margin-right:8px;font-weight:700">&rsaquo;</span>{c}</div>'
            for c in cfg["checks"]
        ])
        st.markdown(f'<div class="section-card"><div class="section-title">Before Trading</div>{checks_html}</div>',
                    unsafe_allow_html=True)

        # ── Exit Check
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
        st.markdown(f'<div class="section-card"><div class="section-title">Open Position Exit Check</div>{exit_html}</div>',
                    unsafe_allow_html=True)

        # ── Portfolio Calculator
        st.markdown("---")
        st.markdown("#### Portfolio Calculator *(optional)*")

        # Auto-select: expiry closest to 7 DTE, strike nearest zone midpoint
        def _best_chain_strike(chain_data, sz_low, sz_high):
            """Find (strike, premium, exp_label) closest to zone mid from 7-DTE expiry."""
            if not chain_data or chain_data.get("error"):
                return None, None, None
            chains = chain_data.get("chains", {})
            if not chains:
                return None, None, None
            # Pick expiry with DTE closest to 7
            import re as _re
            best_exp, best_dte_diff = None, 999
            for lbl in chains:
                m = _re.search(r"(\d+)\s*DTE", lbl)
                if m:
                    dte = int(m.group(1))
                    diff = abs(dte - 7)
                    if diff < best_dte_diff:
                        best_dte_diff = diff
                        best_exp = lbl
            if not best_exp:
                return None, None, None
            puts = chains[best_exp]
            if not puts or isinstance(puts, dict):
                return None, None, None
            liquid = [p for p in puts if p.get("bid", 0) > 0.05]
            if not liquid:
                return None, None, None
            # Zone midpoint
            if sz_low and sz_high:
                zone_mid = (sz_low + sz_high) / 2
            elif sz_high:
                zone_mid = sz_high - 0.75
            else:
                zone_mid = None
            if zone_mid:
                best_put = min(liquid, key=lambda p: abs(p["strike"] - zone_mid))
            else:
                # Fall back: lowest delta above 0.15
                candidates = [p for p in liquid if p.get("delta") and p["delta"] >= 0.15]
                best_put = candidates[-1] if candidates else liquid[-1]
            return best_put["strike"], best_put["mid"], best_exp

        auto_strike, auto_prem, auto_exp = _best_chain_strike(chain_data, sz_low, sz_high)
        default_strike = float(auto_strike) if auto_strike else 45.0
        default_prem   = float(auto_prem)   if auto_prem   else 0.58

        if auto_strike:
            st.caption(
                f"Auto-selected from chain: **{auto_exp}** — "
                f"strike **${auto_strike:.1f}** (nearest zone mid ${(sz_low+sz_high)/2:.1f} "
                f"= (${sz_low:.1f}+${sz_high:.1f})/2)" if sz_low and sz_high
                else f"Auto-selected: {auto_exp} — strike ${auto_strike:.1f}"
            )
        else:
            st.caption("Enter account details for exact sizing.")

        pc1, pc2 = st.columns(2)
        with pc1:
            nlv = st.number_input("Account NLV ($)", min_value=1000, value=42000, step=1000)
            strike_input = st.number_input(
                "Intended Strike ($)",
                min_value=1.0, value=default_strike, step=0.5,
                help="Auto-filled from chain — nearest strike to zone midpoint at closest-to-7-DTE expiry"
            )
        with pc2:
            existing = st.number_input("Existing Assignment ($)", min_value=0, value=0, step=1000)
            premium = st.number_input(
                "Mid Premium / Contract ($)",
                min_value=0.01, value=default_prem, step=0.01,
                help="Auto-filled from chain mid price for the selected strike"
            )

        if dep_pct > 0:
            max_assign = nlv * dep_pct / 100
            available = max(0, max_assign - existing)
            max_contracts = int(available / (strike_input * 100))
            new_assign = max_contracts * strike_input * 100
            total_assign = existing + new_assign
            total_pct = total_assign / nlv * 100
            prem_total = max_contracts * premium * 100
            buffer_req = max(prem_total * 2, nlv * 0.05)
            regime_ok = total_pct <= dep_pct
            # Use same sz_low/sz_high — strike zone is consistent now
            strike_ok = (sz_low is None) or (sz_low <= strike_input <= sz_high + 2)

            if max_contracts <= 0:
                css, status, risk_rating = "calc-fail", "NO ROOM — at or above deployment cap", "ELEVATED"
            elif regime_ok and strike_ok:
                css, status, risk_rating = "calc-pass", "PASSES ALL SIZING RULES", "ACCEPTABLE"
            elif not regime_ok:
                css, status, risk_rating = "calc-warn", "NEAR DEPLOYMENT LIMIT", "ELEVATED"
            else:
                css, status, risk_rating = "calc-warn", "STRIKE OUTSIDE SUGGESTED ZONE", "ELEVATED"

            zone_warn = (
                f'<br><span style="color:#9a3412">Strike ${strike_input:.1f} is outside suggested '
                f'zone ${sz_low:.1f}–${sz_high:.1f}</span>'
                if sz_low and not strike_ok else ""
            )
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
                f'{zone_warn}</div></div>',
                unsafe_allow_html=True,
            )
        else:
            st.info("No bullish CSP deployment in current regime. Inverse spread collateral = spread width × contracts.")

        # ── Option Chains — display only (data already fetched, no re-fetch here)
        if cfg["bullish_csp"] and chain_data:
            st.markdown("---")
            st.markdown("#### TQQQ Put Chains")

            if chain_data.get("error"):
                st.warning("Could not load chains: " + chain_data["error"])
            else:
                # Show auto-detected source values
                auto_parts = []
                if chain_data.get("put_wall"):
                    auto_parts.append(f"Put Wall: ${chain_data['put_wall']:.1f}")
                if chain_data.get("call_wall"):
                    auto_parts.append(f"Call Wall: ${chain_data['call_wall']:.1f}")
                if chain_data.get("gamma_flip"):
                    auto_parts.append(f"Gamma Flip: ${chain_data['gamma_flip']:.2f}")
                if auto_parts:
                    st.caption("Auto-calculated from chain: " + " | ".join(auto_parts))

                for exp_label, puts in chain_data.get("chains", {}).items():
                    st.markdown(f"**{exp_label}**")
                    if isinstance(puts, dict) and "error" in puts:
                        st.caption("Error: " + puts["error"])
                        continue
                    if not puts:
                        st.caption("No data returned for this expiry.")
                        continue

                    filtered = [p for p in puts if p.get("bid", 0) > 0.05][:14]
                    if filtered:
                        st.markdown(
                            '<div class="section-card">'
                            '<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1fr;'
                            'gap:4px;font-size:0.72rem;color:#64748b;font-weight:700;'
                            'padding:6px 0;border-bottom:2px solid #e2e8f0;margin-bottom:4px">'
                            '<span>Strike</span><span>Bid</span><span>Ask</span>'
                            '<span>Mid</span><span>Delta</span></div>',
                            unsafe_allow_html=True,
                        )
                        rows_html = ""
                        for p in filtered:
                            in_zone = sz_low and sz_high and sz_low <= p["strike"] <= sz_high
                            bg = "background:#f0fdf4;" if in_zone else ""
                            delta_str = f"{p['delta']:.3f}" if p.get("delta") else "—"
                            rows_html += (
                                f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1fr;'
                                f'gap:4px;font-size:0.8rem;padding:5px 0;border-bottom:1px solid #f1f5f9;{bg}">'
                                f'<span style="font-weight:{"700" if in_zone else "400"};'
                                f'color:{"#166534" if in_zone else "#1e293b"}">'
                                f'${p["strike"]:.1f}{"  ✓" if in_zone else ""}</span>'
                                f'<span style="color:#334155">${p["bid"]:.2f}</span>'
                                f'<span style="color:#334155">${p["ask"]:.2f}</span>'
                                f'<span style="font-weight:600;color:#1e293b">${p["mid"]:.2f}</span>'
                                f'<span style="color:#64748b">{delta_str}</span>'
                                f'</div>'
                            )
                        st.markdown(rows_html + "</div>", unsafe_allow_html=True)
                        if sz_low and sz_high:
                            st.caption(f"✓ = within suggested strike zone (${sz_low:.1f}–${sz_high:.1f})")
                    else:
                        st.caption("No liquid strikes found for this expiry.")

        # ── Copy for AI
        st.markdown("---")
        st.markdown("#### Copy for AI")
        st.caption("Select all and paste directly into Claude or Compa.")
        summary = build_summary(
            regime, confidence, sig_label, qqq, sma, pct, vix, vix_sym,
            vix_reliable, rsi, cfg, dep_pct, dep_note, breadth_val,
            gamma_above, gamma_flip_val, put_wall_val, call_wall_val,
            tqqq_price, sz_low, sz_high, et_now,
            chain_data=chain_data,
        )
        st.text_area("", value=summary, height=400, label_visibility="collapsed")
        st.success("Report generated at " + et_now.strftime("%I:%M %p ET"))

st.divider()
st.caption("V10 Playbook · For personal use only · Not financial advice")
