"""
PredEdge Market Intelligence — FastAPI Backend v4
Real stock data + Polymarket odds + portfolio + visitor analytics
"""
import os, json, time, asyncio, logging, hashlib, uuid
from pathlib import Path
from datetime import datetime, date
from typing import Optional
from collections import defaultdict
import yfinance as yf
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prededge")
app = FastAPI(title="PredEdge", version="4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Stock Universe ──
UNIVERSE = {
    "quantum": [
        {"t":"IONQ","name":"IonQ Inc","desc":"Trapped-ion quantum computing","sector":"quantum"},
        {"t":"RGTI","name":"Rigetti Computing","desc":"Superconducting quantum processors","sector":"quantum"},
        {"t":"QBTS","name":"D-Wave Quantum","desc":"Quantum annealing systems","sector":"quantum"},
        {"t":"QUBT","name":"Quantum Computing Inc","desc":"Quantum-ready software","sector":"quantum"},
        {"t":"ARQQ","name":"Arqit Quantum","desc":"Quantum encryption platform","sector":"quantum"},
        {"t":"IBM","name":"IBM","desc":"Heron chip, 1000+ qubits","sector":"quantum"},
        {"t":"GOOG","name":"Alphabet","desc":"Willow quantum chip","sector":"quantum"},
    ],
    "ai": [
        {"t":"NVDA","name":"NVIDIA Corp","desc":"GPU / AI training dominance","sector":"ai"},
        {"t":"AMD","name":"Adv Micro Devices","desc":"MI300X AI accelerator","sector":"ai"},
        {"t":"AVGO","name":"Broadcom Inc","desc":"Networking + custom AI silicon","sector":"ai"},
        {"t":"TSM","name":"TSMC","desc":"Fabricates all leading AI chips","sector":"ai"},
        {"t":"MRVL","name":"Marvell Technology","desc":"Custom AI accelerators","sector":"ai"},
        {"t":"SMCI","name":"Super Micro Computer","desc":"AI server infrastructure","sector":"ai"},
        {"t":"VRT","name":"Vertiv Holdings","desc":"Data centre power & cooling","sector":"ai"},
        {"t":"ANET","name":"Arista Networks","desc":"Data centre networking","sector":"ai"},
        {"t":"DELL","name":"Dell Technologies","desc":"AI servers & edge computing","sector":"ai"},
        {"t":"MSFT","name":"Microsoft","desc":"Azure AI + OpenAI partnership","sector":"ai"},
    ],
    "etf": [
        {"t":"QTUM","name":"Defiance Quantum ETF","desc":"Quantum computing & ML","sector":"etf","tracks":"quantum"},
        {"t":"SMH","name":"VanEck Semiconductor","desc":"Top 25 US semiconductors","sector":"etf","tracks":"ai"},
        {"t":"SOXX","name":"iShares Semiconductor","desc":"ICE Semiconductor Index","sector":"etf","tracks":"ai"},
        {"t":"QQQ","name":"Invesco QQQ Trust","desc":"Nasdaq-100 tracker","sector":"etf","tracks":"ai"},
        {"t":"ARKQ","name":"ARK Autonomous Tech","desc":"Robotics, AI, energy storage","sector":"etf","tracks":"ai"},
        {"t":"BOTZ","name":"Global X Robotics & AI","desc":"Robotics and AI companies","sector":"etf","tracks":"ai"},
        {"t":"XLK","name":"Technology Select SPDR","desc":"S&P 500 tech sector","sector":"etf","tracks":"ai"},
    ],
}
ALL_TICKERS = []
TICKER_META = {}
for sector, stocks in UNIVERSE.items():
    for s in stocks:
        ALL_TICKERS.append(s["t"])
        TICKER_META[s["t"]] = s

ETF_HOLDINGS = {
    "QTUM": {"IONQ":4.8,"RGTI":2.1,"QBTS":1.5,"QUBT":0.8,"ARQQ":0.6,"IBM":5.2,"GOOG":3.9,"MSFT":4.1,"NVDA":3.5,"AMD":2.8},
    "SMH": {"NVDA":20.5,"TSM":12.3,"AVGO":8.1,"AMD":5.4,"MRVL":3.2,"ANET":2.1,"DELL":1.8},
    "SOXX": {"NVDA":8.9,"AMD":8.2,"AVGO":7.8,"TSM":5.5,"MRVL":4.6,"SMCI":1.2},
    "QQQ": {"NVDA":8.2,"MSFT":8.8,"GOOG":5.1,"AMD":1.4,"AVGO":2.1,"ANET":0.6},
    "ARKQ": {"NVDA":3.2,"MSFT":2.8,"DELL":4.5,"VRT":2.1,"SMCI":1.5},
    "BOTZ": {"NVDA":7.8,"IONQ":1.2,"IBM":3.5,"MSFT":2.4},
    "XLK": {"NVDA":15.2,"MSFT":14.8,"AVGO":5.1,"AMD":2.3,"ANET":0.9,"DELL":0.8,"IBM":1.2},
}
def get_etf_exposure(ticker):
    exp = []
    for etf, holdings in ETF_HOLDINGS.items():
        if ticker in holdings:
            exp.append({"etf": etf, "weight": holdings[ticker]})
    exp.sort(key=lambda x: x["weight"], reverse=True)
    return exp

# ── Price Cache ──
price_cache = {}
cache_ts = 0

async def fetch_all_prices():
    global price_cache, cache_ts
    now = time.time()
    if now - cache_ts < 30 and price_cache:
        return price_cache
    results = {}
    try:
        tickers_str = " ".join(ALL_TICKERS)
        data = yf.download(tickers_str, period="1y", interval="1d", group_by="ticker", progress=False, threads=True)
        for ticker in ALL_TICKERS:
            meta = TICKER_META[ticker]
            try:
                if ticker not in data.columns.get_level_values(0):
                    continue
                df = data[ticker].dropna(subset=["Close"])
                if len(df) < 2:
                    continue
                closes = [round(float(c), 2) for c in df["Close"].tolist()]
                current = closes[-1]
                prev_close = closes[-2]
                volume = int(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0
                change = round(current - prev_close, 2)
                change_pct = round((change / prev_close * 100) if prev_close else 0, 2)
                highs = [float(h) for h in df["High"].tolist() if h == h]
                lows = [float(l) for l in df["Low"].tolist() if l == l]
                w52_high = round(max(highs), 2) if highs else current
                w52_low = round(min(lows), 2) if lows else current
                w52_pct = round((current - w52_low) / (w52_high - w52_low) * 100, 1) if w52_high != w52_low else 50
                try:
                    jan = [float(c) for c, d in zip(df["Close"].tolist(), df.index) if hasattr(d, 'month') and d.month == 1 and c == c]
                    ytd_start = jan[0] if jan else closes[0]
                except:
                    ytd_start = closes[0]
                ytd_pct = round((current - ytd_start) / ytd_start * 100, 2) if ytd_start else 0
                rsi_data = closes[-15:] if len(closes) >= 15 else closes
                g, l = 0, 0
                for i in range(1, len(rsi_data)):
                    d = rsi_data[i] - rsi_data[i-1]
                    if d > 0: g += d
                    else: l -= d
                rs = g / l if l > 0 else 10
                rsi = round(100 - 100 / (1 + rs), 1)
                rh = max(closes[-10:]) if len(closes) >= 10 else max(closes)
                dd = round((current - rh) / rh * 100, 2)
                signal, reason = "HOLD", f"RSI {rsi}, neutral"
                if dd < -5 and rsi < 35: signal, reason = "BUY", f"Oversold: RSI {rsi}, {dd}% from high"
                elif dd < -3 and change_pct < -1.5: signal, reason = "BUY", f"Dip: {change_pct}% today, {dd}% drawdown"
                elif rsi < 30: signal, reason = "BUY", f"RSI oversold at {rsi}"
                elif rsi > 75 and change_pct > 2: signal, reason = "SELL", f"Overbought: RSI {rsi}, +{change_pct}%"
                elif rsi > 70: signal, reason = "SELL", f"RSI elevated at {rsi}"
                elif change_pct > 3: signal, reason = "HOLD", f"Strong +{change_pct}%, RSI {rsi}"
                elif change_pct < -2: signal, reason = "HOLD", f"Weak {change_pct}%, RSI {rsi}, watch"
                results[ticker] = {
                    "ticker": ticker, "name": meta["name"], "desc": meta["desc"],
                    "sector": meta["sector"], "tracks": meta.get("tracks", ""),
                    "price": current, "prevClose": prev_close,
                    "change": change, "changePct": change_pct, "volume": volume,
                    "history": closes[-20:],
                    "w52High": w52_high, "w52Low": w52_low, "w52RangePct": w52_pct,
                    "ytdPct": ytd_pct, "etfExposure": get_etf_exposure(ticker),
                    "rsi": rsi, "drawdown": dd,
                    "signal": signal, "reason": reason, "timestamp": now,
                }
            except Exception as e:
                logger.warning(f"Error {ticker}: {e}")
        if results:
            price_cache = results
            cache_ts = now
            logger.info(f"Prices: {len(results)}/{len(ALL_TICKERS)}")
    except Exception as e:
        logger.error(f"Price fetch error: {e}")
    return price_cache


# ── Financial Inclusion Whitelist (from ETF prediction market project) ──
FINANCIAL_KEYWORDS = {
    # Central banks & monetary policy
    "fed", "federal reserve", "fomc", "rate cut", "rate hike", "interest rate",
    "monetary policy", "quantitative", "tightening", "easing", "dovish", "hawkish",
    "ecb", "bank of england", "boe", "boj", "central bank",
    # Inflation & prices
    "inflation", "cpi", "pce", "deflation", "stagflation", "consumer price",
    # Growth & recession
    "recession", "gdp", "economic growth", "soft landing", "hard landing",
    "unemployment", "jobs report", "nonfarm", "payroll",
    # Fiscal & government
    "tariff", "trade war", "sanctions", "debt ceiling", "government shutdown",
    "fiscal", "deficit", "stimulus",
    # Commodities
    "oil", "crude", "brent", "wti", "opec", "natural gas",
    "gold", "silver", "copper", "commodity",
    # Credit & fixed income
    "default", "credit spread", "treasury", "bond", "yield", "sovereign debt",
    # Equities & indices
    "s&p 500", "s&p500", "nasdaq", "dow jones", "stock market", "bear market",
    "bull market", "correction", "earnings",
    # Tech & AI
    "nvidia", "nvda", "semiconductor", "chip", "export ban", "export restriction",
    "ai regulation", "artificial intelligence", "quantum computing", "quantum",
    "openai", "chatgpt", "tech regulation", "antitrust",
    # Sector
    "fda", "drug approval", "biotech", "energy", "renewable", "nuclear", "solar",
    "bank failure", "banking crisis", "financial crisis",
    # Crypto (financial relevance)
    "bitcoin", "ethereum", "crypto regulation", "btc",
    # Regional economics
    "china gdp", "eurozone", "emerging market", "dollar", "euro", "sterling", "yen",
    # Company-specific
    "apple", "microsoft", "google", "amazon", "meta", "tesla",
    "tsmc", "amd", "broadcom", "intel",
}

# Relevance categories — mapped from keywords to stock-relevant tags
RELEVANCE_MAP = [
    (["fed", "fomc", "rate cut", "rate hike", "interest rate", "federal reserve", "monetary policy", "dovish", "hawkish", "ecb", "bank of england", "boe", "central bank"], "RATES", "green"),
    (["recession", "soft landing", "hard landing", "economic growth"], "RECESSION RISK", "amber"),
    (["nvidia", "nvda"], "NVDA DIRECT", "green"),
    (["semiconductor", "chip", "export ban", "export restriction", "tsmc", "amd", "broadcom", "intel"], "SEMIS", "green"),
    (["bitcoin", "btc", "ethereum", "crypto regulation"], "CRYPTO", "amber"),
    (["inflation", "cpi", "pce", "deflation", "stagflation", "consumer price"], "INFLATION", "green"),
    (["artificial intelligence", "ai regulation", "openai", "chatgpt", "quantum computing", "quantum"], "AI / QUANTUM", "green"),
    (["gdp", "unemployment", "jobs report", "nonfarm", "payroll"], "MACRO DATA", "green"),
    (["tariff", "trade war", "sanctions"], "TRADE RISK", "red"),
    (["oil", "crude", "brent", "wti", "opec", "natural gas", "gold", "silver", "copper", "commodity"], "COMMODITIES", "amber"),
    (["s&p 500", "s&p500", "nasdaq", "dow jones", "stock market", "bear market", "bull market", "correction"], "INDEX", "green"),
    (["treasury", "bond", "yield", "sovereign debt", "credit spread", "default"], "FIXED INCOME", "amber"),
    (["debt ceiling", "government shutdown", "fiscal", "deficit", "stimulus"], "FISCAL POLICY", "amber"),
    (["apple", "microsoft", "google", "amazon", "meta", "tesla"], "BIG TECH", "green"),
    (["fda", "drug approval", "biotech"], "BIOTECH", "amber"),
    (["energy", "renewable", "nuclear", "solar"], "ENERGY", "amber"),
    (["bank failure", "banking crisis", "financial crisis"], "FIN CRISIS", "red"),
    (["dollar", "euro", "sterling", "yen", "eurozone", "emerging market"], "FX / EM", "amber"),
    (["earnings"], "EARNINGS", "green"),
]

def classify_market(title_lower):
    """
    Inclusion-based market classifier.
    Returns (relevance_tag, color) if the market is financially relevant.
    Returns (None, None) if it should be excluded (sports, entertainment, etc).
    """
    # Gate: must contain at least one financial keyword
    has_financial = False
    for kw in FINANCIAL_KEYWORDS:
        if kw in title_lower:
            has_financial = True
            break
    if not has_financial:
        return None, None

    # Find the best relevance category
    for keywords, tag, color in RELEVANCE_MAP:
        for kw in keywords:
            if kw in title_lower:
                return tag, color

    # Passed the financial gate but no specific category — general financial
    return "FINANCIAL", "neutral"

# ── Polymarket ──
poly_cache = []
poly_ts = 0

async def fetch_polymarket():
    global poly_cache, poly_ts
    now = time.time()
    if now - poly_ts < 60 and poly_cache:
        return poly_cache

    results = []

    # ── Strategy: bulk fetch 200 top markets by volume, filter locally ──
    # Polymarket's search/tag API params are unreliable.
    # Fetch by volume, apply inclusion whitelist client-side.
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for offset in [0, 100]:
            try:
                resp = await client.get("https://gamma-api.polymarket.com/markets",
                    params={"active": "true", "closed": "false",
                            "limit": "100", "offset": str(offset),
                            "order": "volume", "ascending": "false"})
                if resp.status_code != 200:
                    logger.warning(f"Polymarket offset={offset}: status {resp.status_code}")
                    continue
                items = resp.json()
                if not isinstance(items, list):
                    items = items.get("data", [])

                for m in items:
                    question = m.get("question", "")
                    if not question:
                        continue
                    ql = question.lower()

                    # ── EXCLUSION 1: Sports matchups (catches "Team A vs Team B") ──
                    if " vs " in ql or " vs. " in ql:
                        continue

                    # ── EXCLUSION 2: Tiny blocklist for edge cases ──
                    BLOCK = ["mvp", "award", "temperature", "weather", "winner of",
                             "touchdown", "home run", "goal scored", "world cup",
                             "premier league", "nba", "nfl", "nhl", "mlb",
                             "oscar", "grammy", "emmy", "bachelor"]
                    if any(b in ql for b in BLOCK):
                        continue

                    # Parse prices
                    try:
                        ps = m.get("outcomePrices", "[]")
                        pp = json.loads(ps) if isinstance(ps, str) else (ps or [])
                    except:
                        continue
                    if len(pp) < 2:
                        continue
                    yp = float(pp[0] or 0)
                    np = float(pp[1] or 0)

                    # Skip near-resolved markets
                    if yp < 0.05 or yp > 0.95:
                        continue

                    # ── INCLUSION GATE: must contain a financial keyword ──
                    # This is the primary filter. "war" excluded as standalone
                    # (matched "Warsaw") — kept only as "trade war".
                    FIN_KW = [
                        # Monetary policy
                        "fed", "federal reserve", "fomc", "rate cut", "rate hike",
                        "interest rate", "central bank", "ecb",
                        # Inflation
                        "inflation", "cpi", "pce", "deflation",
                        # Growth
                        "recession", "gdp", "unemployment", "jobs report", "payroll",
                        # Fiscal / geopolitical
                        "tariff", "trade war", "sanctions", "debt ceiling",
                        "government shutdown",
                        # Commodities
                        "oil", "crude", "brent", "opec", "natural gas",
                        "gold price", "silver price", "commodity",
                        # Equities / indices
                        "s&p", "nasdaq", "dow jones", "stock market",
                        "bear market", "bull market", "earnings",
                        # Tech / AI
                        "nvidia", "semiconductor", "chip export",
                        "artificial intelligence", "ai regulation",
                        "quantum computing", "openai", "chatgpt",
                        # Companies
                        "apple", "microsoft", "google", "amazon", "meta",
                        "tesla", "tsmc", "amd", "broadcom",
                        # Crypto
                        "bitcoin", "btc", "ethereum", "crypto",
                        # FX
                        "dollar", "euro ", "sterling", "yen",
                    ]

                    matched_kw = None
                    for kw in FIN_KW:
                        if kw in ql:
                            matched_kw = kw
                            break
                    if not matched_kw:
                        continue

                    # ── Classify relevance ──
                    rel, rc = classify_market(ql)
                    if not rel:
                        rel, rc = "FINANCIAL", "neutral"

                    vol = float(m.get("volume", 0) or 0)
                    vol24 = float(m.get("volume24hr", 0) or 0)

                    results.append({
                        "question": question, "tag": matched_kw,
                        "yesPrice": round(yp, 3), "noPrice": round(np, 3),
                        "volume": vol, "vol24": vol24,
                        "relevance": rel, "relColor": rc,
                    })

                logger.info(f"Polymarket offset={offset}: {len(items)} raw, running total {len(results)} financial")

            except Exception as e:
                logger.warning(f"Polymarket fetch error: {e}")

    # Deduplicate
    seen = set()
    unique = [r for r in results if r["question"] not in seen and not seen.add(r["question"])]
    unique.sort(key=lambda x: x["vol24"], reverse=True)

    if unique:
        poly_cache = unique
        poly_ts = now
    logger.info(f"Polymarket final: {len(unique)} financial markets from 200 scanned")
    return poly_cache

# ── Portfolio ──
PF = "portfolio.json"
def load_pf():
    try:
        with open(PF) as f: return json.load(f)
    except: return []
def save_pf(d):
    with open(PF, "w") as f: json.dump(d, f, indent=2)

class PosIn(BaseModel):
    ticker: str; qty: float; costBasis: float; notes: Optional[str] = ""

# ── Analytics ──
ANALYTICS_FILE = "analytics.json"

def load_analytics():
    try:
        with open(ANALYTICS_FILE) as f: return json.load(f)
    except: return {"visitors": {}, "daily": {}, "total_views": 0}

def save_analytics(data):
    with open(ANALYTICS_FILE, "w") as f: json.dump(data, f, indent=2)

def record_visit(visitor_id: str, user_agent: str, ip: str):
    """Record a visit. Tracks unique visitors, daily views, and return rate."""
    a = load_analytics()
    today = date.today().isoformat()
    now_str = datetime.now().isoformat()

    # Track unique visitor
    if visitor_id not in a["visitors"]:
        a["visitors"][visitor_id] = {
            "first_seen": now_str,
            "last_seen": now_str,
            "visit_count": 1,
            "user_agent": user_agent[:200],
            "days_active": [today],
        }
    else:
        v = a["visitors"][visitor_id]
        v["last_seen"] = now_str
        v["visit_count"] = v.get("visit_count", 0) + 1
        days = v.get("days_active", [])
        if today not in days:
            days.append(today)
            v["days_active"] = days[-30:]  # Keep last 30 days

    # Track daily totals
    if today not in a["daily"]:
        a["daily"][today] = {"views": 0, "unique": []}
    a["daily"][today]["views"] += 1
    if visitor_id not in a["daily"][today]["unique"]:
        a["daily"][today]["unique"].append(visitor_id)

    # Keep only last 90 days of daily data
    sorted_days = sorted(a["daily"].keys())
    if len(sorted_days) > 90:
        for old_day in sorted_days[:-90]:
            del a["daily"][old_day]

    a["total_views"] = a.get("total_views", 0) + 1
    save_analytics(a)

def get_analytics_summary():
    """Get analytics summary for the admin view."""
    a = load_analytics()
    today = date.today().isoformat()
    total_visitors = len(a.get("visitors", {}))
    total_views = a.get("total_views", 0)

    # Return visitors = visited on more than 1 distinct day
    return_visitors = sum(1 for v in a.get("visitors", {}).values()
                         if len(v.get("days_active", [])) > 1)

    # Today's stats
    today_data = a.get("daily", {}).get(today, {"views": 0, "unique": []})
    today_views = today_data["views"]
    today_unique = len(today_data["unique"])

    # Last 7 days
    daily = a.get("daily", {})
    last_7 = sorted(daily.keys())[-7:]
    week_views = sum(daily[d]["views"] for d in last_7 if d in daily)
    week_unique = len(set(vid for d in last_7 if d in daily for vid in daily[d]["unique"]))

    # Daily breakdown for chart
    last_14 = sorted(daily.keys())[-14:]
    daily_chart = [{"date": d, "views": daily[d]["views"], "unique": len(daily[d]["unique"])}
                   for d in last_14 if d in daily]

    # Top visitors by visit count
    top_visitors = sorted(a.get("visitors", {}).items(),
                          key=lambda x: x[1].get("visit_count", 0), reverse=True)[:10]
    top_list = [{"id": vid[:12]+"...", "visits": v["visit_count"],
                 "first": v.get("first_seen", "")[:10], "last": v.get("last_seen", "")[:10],
                 "days": len(v.get("days_active", []))}
                for vid, v in top_visitors]

    return {
        "totalVisitors": total_visitors,
        "totalViews": total_views,
        "returnVisitors": return_visitors,
        "returnRate": round(return_visitors / total_visitors * 100, 1) if total_visitors else 0,
        "todayViews": today_views,
        "todayUnique": today_unique,
        "weekViews": week_views,
        "weekUnique": week_unique,
        "dailyChart": daily_chart,
        "topVisitors": top_list,
    }

# ── Endpoints ──
@app.get("/api/prices")
async def api_prices():
    return {"prices": await fetch_all_prices(), "timestamp": time.time()}

@app.get("/api/polymarket")
async def api_poly():
    m = await fetch_polymarket()
    return {"markets": m, "count": len(m), "timestamp": time.time()}

@app.get("/api/portfolio")
async def api_pf():
    pf = load_pf(); prices = await fetch_all_prices()
    enriched = []
    for p in pf:
        cur = prices.get(p["ticker"], {}).get("price", p["costBasis"])
        mv = cur * p["qty"]; cost = p["costBasis"] * p["qty"]
        enriched.append({**p, "currentPrice": cur, "mktValue": round(mv,2), "pnl": round(mv-cost,2), "pnlPct": round((mv-cost)/cost*100 if cost else 0, 2)})
    tv = sum(e["mktValue"] for e in enriched); tc = sum(e["costBasis"]*e["qty"] for e in enriched)
    return {"positions": enriched, "totalValue": round(tv,2), "totalCost": round(tc,2), "totalPnl": round(tv-tc,2)}

@app.post("/api/portfolio")
async def api_add(pos: PosIn):
    pf = load_pf()
    ex = next((i for i,p in enumerate(pf) if p["ticker"]==pos.ticker), None)
    if ex is not None:
        old=pf[ex]; tq=old["qty"]+pos.qty; ac=(old["costBasis"]*old["qty"]+pos.costBasis*pos.qty)/tq
        pf[ex]={"ticker":pos.ticker,"qty":tq,"costBasis":round(ac,2),"date":datetime.now().strftime("%Y-%m-%d"),"notes":pos.notes or old.get("notes","")}
    else:
        pf.append({"ticker":pos.ticker,"qty":pos.qty,"costBasis":pos.costBasis,"date":datetime.now().strftime("%Y-%m-%d"),"notes":pos.notes or ""})
    save_pf(pf); return {"status":"ok"}

@app.delete("/api/portfolio/{ticker}")
async def api_del(ticker: str):
    save_pf([p for p in load_pf() if p["ticker"] != ticker.upper()]); return {"status":"ok"}

@app.post("/api/track")
async def api_track(request: Request):
    """Record a page visit for analytics."""
    body = await request.json()
    visitor_id = body.get("vid", str(uuid.uuid4()))
    ua = request.headers.get("user-agent", "unknown")[:200]
    ip = request.client.host if request.client else "unknown"
    record_visit(visitor_id, ua, ip)
    return {"status": "ok"}

@app.get("/api/analytics")
async def api_analytics():
    """Get analytics summary. Access via /?stats at the end of the URL."""
    return get_analytics_summary()

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    p = Path(__file__).parent / "index.html"
    return HTMLResponse(p.read_text()) if p.exists() else HTMLResponse("<h1>PredEdge running</h1>")

@app.on_event("startup")
async def startup():
    logger.info(f"PredEdge v4 — {len(ALL_TICKERS)} tickers + analytics")
    asyncio.create_task(fetch_all_prices())
    asyncio.create_task(fetch_polymarket())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
