"""
data_layer.py — Pure data functions (no Streamlit).
Replaces @st.cache_data with a simple TTL cache.
Replaces st.secrets with os.environ.
"""
import os, re, io, time, json, base64
import requests, numpy as np, pandas as pd
from datetime import datetime
from functools import wraps
import FinanceDataReader as fdr

# ── Constants ──────────────────────────────────────────────────────────────────
GITHUB_REPO    = "shinkiyeol9814-droid/stockapp"   # source data (original repo)
GITHUB_BRANCH  = "main"
WATCHLIST_FILE = "data/watchlist/watchlist.json"
ESTIMATES_FILE = "data/valuation/user_estimates.json"
UNIT           = 100_000_000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
API_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

METHODS  = ["POR(영업익)", "PER(순이익)", "PBR(자본총계)", "EV/EBITDA"]
COL_MAP  = {
    "POR(영업익)":   "영업이익",
    "PER(순이익)":   "당기순이익",
    "PBR(자본총계)": "자본총계",
    "EV/EBITDA":     "EV/EBITDA",
}
CUR_YEAR  = datetime.today().year
NEXT_YEAR = 2027

# ── TTL cache ──────────────────────────────────────────────────────────────────
_STORE: dict = {}

def ttl_cache(seconds: int):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args):
            key = (fn.__qualname__,) + tuple(str(a) for a in args)
            if key in _STORE:
                result, ts = _STORE[key]
                if time.time() - ts < seconds:
                    return result
            result = fn(*args)
            _STORE[key] = (result, time.time())
            return result
        def clear():
            dead = [k for k in list(_STORE) if k[0] == fn.__qualname__]
            for k in dead:
                _STORE.pop(k, None)
        wrapper.clear = clear
        return wrapper
    return decorator

# ── GitHub helpers ─────────────────────────────────────────────────────────────
def _gh_headers():
    tok = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN", "")
    return {"Authorization": f"token {tok}", "Accept": "application/vnd.github.v3+json"}

def save_to_github(file_path: str, content: str, message: str):
    try:
        url  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        hdrs = _gh_headers()
        res  = requests.get(url, headers=hdrs, params={"ref": GITHUB_BRANCH}, timeout=7)
        sha  = res.json().get("sha") if res.status_code == 200 else None
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch":  GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(url, headers=hdrs, json=payload, timeout=10)
        if r.status_code in [200, 201]:
            return True, "성공"
        return False, r.text
    except Exception as e:
        return False, str(e)

# ── Watchlist ──────────────────────────────────────────────────────────────────
@ttl_cache(seconds=60)
def load_watchlist() -> dict:
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{WATCHLIST_FILE}?ref={GITHUB_BRANCH}"
        res = requests.get(url, headers=_gh_headers(), timeout=7)
        if res.status_code == 200:
            return json.loads(base64.b64decode(res.json()["content"]).decode())
    except:
        pass
    return {}

def save_watchlist(data: dict) -> bool:
    load_watchlist.clear()
    b64 = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()
    url  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{WATCHLIST_FILE}"
    hdrs = _gh_headers()
    res  = requests.get(url, headers=hdrs, params={"ref": GITHUB_BRANCH}, timeout=7)
    sha  = res.json().get("sha") if res.status_code == 200 else None
    payload = {"message": "Update watchlist", "content": b64, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=hdrs, json=payload, timeout=10)
    return r.status_code in [200, 201]

# ── Live price ─────────────────────────────────────────────────────────────────
@ttl_cache(seconds=60)
def get_live_price(code: str):
    try:
        data   = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/basic",
            headers=API_HEADERS, timeout=5
        ).json()
        price  = float(str(data.get("closePrice")  or "0").replace(",", ""))
        if price == 0:
            price = float(str(data.get("stockEndPrice") or "0").replace(",", ""))
        change = float(str(data.get("fluctuationsRatio") or "0").replace(",", ""))
        name   = data.get("stockName") or data.get("corporateName", code)
        return (price if price > 0 else None), change, name
    except:
        return None, None, code

# ── KRX listing ───────────────────────────────────────────────────────────────
@ttl_cache(seconds=86400)
def get_ticker_listing():
    for _ in range(3):
        try:
            df = fdr.StockListing("KRX")
            if not df.empty and "Name" in df.columns:
                return df
        except:
            pass
    try:
        url = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
        res = requests.get(url, headers=HEADERS, timeout=10)
        df  = pd.read_html(io.StringIO(res.text), header=0)[0]
        df  = df.rename(columns={"회사명": "Name", "종목코드": "Code"})
        df["Code"] = df["Code"].astype(str).str.zfill(6)
        return df
    except:
        return pd.DataFrame(columns=["Code", "Name"])

def get_stocks_count(ticker_row, ticker: str):
    try:
        if "Stocks" in ticker_row.columns:
            sc = pd.to_numeric(ticker_row["Stocks"].values[0], errors="coerce")
            if pd.notna(sc) and sc > 0:
                return sc
    except:
        pass
    try:
        url = f"https://m.stock.naver.com/api/stock/{ticker}/integration"
        res = requests.get(url, headers=API_HEADERS, timeout=5).json()
        return int(res["stockEndType"]["totalInfo"]["stockCount"])
    except:
        pass
    try:
        url   = f"https://finance.naver.com/item/main.naver?code={ticker}"
        res   = requests.get(url, headers=API_HEADERS, timeout=5)
        match = re.search(r"상장주식수<.*?<em>([\d,]+)</em>", res.text, re.DOTALL)
        if match:
            return int(match.group(1).replace(",", ""))
    except:
        pass
    try:
        if "Marcap" in ticker_row.columns and "Close" in ticker_row.columns:
            mc = pd.to_numeric(ticker_row["Marcap"].values[0], errors="coerce")
            cp = pd.to_numeric(ticker_row["Close"].values[0],  errors="coerce")
            if pd.notna(mc) and pd.notna(cp) and mc > 0 and cp > 0:
                return int(mc / cp)
    except:
        pass
    return 1

@ttl_cache(seconds=3600)
def get_watch_financials(code: str):
    try:
        listing    = get_ticker_listing()
        ticker_row = listing[listing["Code"].astype(str).str.zfill(6) == code.zfill(6)]
        if ticker_row.empty:
            return None, 0
        stocks = get_stocks_count(ticker_row, code)
        fin_df = get_hybrid_financials(code)
        return fin_df, int(stocks)
    except:
        return None, 0

@ttl_cache(seconds=3600)
def get_stock_price_data(ticker: str, start_date: str, end_date: str):
    try:
        return fdr.DataReader(ticker, start_date, end_date)
    except:
        return pd.DataFrame()

# ── Financial parsers ──────────────────────────────────────────────────────────
def parse_fin_table(html: str):
    try:
        dfs = pd.read_html(io.StringIO(html))
        for df in dfs:
            if "IFRS" in " ".join([str(c) for c in df.columns]):
                df        = df.copy()
                df.index  = df.iloc[:, 0].astype(str).str.strip().str.replace(" ", "")
                date_cols = [c for c in df.columns if re.search(r"\d{4}", str(c))]
                return df[date_cols]
    except:
        pass
    return None

def get_val(df_parsed, row_pattern: str, col):
    for k in df_parsed.index:
        if re.search(row_pattern, str(k), re.I):
            try:
                val = df_parsed.loc[k, col]
                if isinstance(val, pd.Series):
                    val = val.dropna()
                    val = val.iloc[0] if len(val) > 0 else np.nan
                cleaned = re.sub(r"[^\d\.-]", "", str(val))
                if cleaned and cleaned not in ["-", ".", "-."]:
                    return float(cleaned)
            except:
                pass
    return np.nan

def parse_consensus_value(s):
    if s is None:
        return np.nan
    s_clean = str(s).replace(",", "").strip()
    if s_clean in ["", "-", "N/A", "n/a"]:
        return np.nan
    try:
        return float(s_clean)
    except:
        return np.nan

def fetch_consensus_data(ticker: str):
    try:
        today_str = datetime.today().strftime("%Y%m%d")
        url = (
            f"https://comp.wisereport.co.kr/company/ajax/c1050001_data.aspx"
            f"?flag=2&cmp_cd={ticker}&finGubun=MAIN&frq=0&sDT={today_str}&chartType=svg"
        )
        hdrs = HEADERS.copy()
        hdrs["Referer"] = f"https://comp.wisereport.co.kr/company/c1050001.aspx?cmp_cd={ticker}"
        res = requests.get(url, headers=hdrs, timeout=7)
        if res.status_code != 200:
            return None
        return res.json().get("JsonData", [])
    except:
        return None

@ttl_cache(seconds=3600)
def get_hybrid_financials(ticker: str) -> pd.DataFrame:
    target_years = [2021, 2022, 2023, 2024, 2025, 2026, 2027]
    master: dict = {
        y: {"매출액": np.nan, "영업이익": np.nan, "당기순이익": np.nan,
            "자본총계": np.nan, "EV/EBITDA": np.nan}
        for y in target_years
    }
    try:
        main_url  = f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={ticker}"
        main_res  = requests.get(main_url, headers=HEADERS, timeout=7)
        encparam  = ""
        m = re.search(r"encparam\s*:\s*'([^']+)'", main_res.text)
        if m:
            encparam = m.group(1)
        ajax_hdrs = HEADERS.copy()
        ajax_hdrs["Referer"] = main_url

        for url in [
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF2001.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
        ]:
            res       = requests.get(url, headers=ajax_hdrs, timeout=7)
            df_parsed = parse_fin_table(res.text)
            if df_parsed is None:
                continue
            for c in df_parsed.columns:
                mc = re.search(r"(20\d{2})", str(c))
                if not mc:
                    continue
                y = int(mc.group(1))
                if y not in target_years:
                    continue
                r   = get_val(df_parsed, r"^(매출액|영업수익)", c)
                o   = get_val(df_parsed, r"^영업이익$", c)
                if pd.isna(o):
                    o = get_val(df_parsed, r"^영업이익\(발표기준\)", c)
                n   = get_val(df_parsed, r"^(당기순이익|지배주주순이익)", c)
                cap = get_val(df_parsed, r"^(자본총계|지배주주지분)", c)
                if pd.isna(master[y]["매출액"])     and pd.notna(r):   master[y]["매출액"]   = r
                if pd.isna(master[y]["영업이익"])   and pd.notna(o):   master[y]["영업이익"] = o
                if pd.isna(master[y]["당기순이익"]) and pd.notna(n):   master[y]["당기순이익"] = n
                if pd.isna(master[y]["자본총계"])   and pd.notna(cap): master[y]["자본총계"] = cap

        consensus = fetch_consensus_data(ticker)
        if consensus:
            for row_json in consensus:
                ym = row_json.get("YYMM", "")
                mc = re.search(r"(20\d{2})", ym)
                if not mc:
                    continue
                y    = int(mc.group(1))
                if y not in target_years:
                    continue
                sales = parse_consensus_value(row_json.get("SALES"))
                op    = parse_consensus_value(row_json.get("OP"))
                np_v  = parse_consensus_value(row_json.get("NP"))
                ev    = parse_consensus_value(row_json.get("EV"))
                if pd.isna(master[y]["매출액"])     and pd.notna(sales): master[y]["매출액"]     = sales
                if pd.isna(master[y]["영업이익"])   and pd.notna(op):    master[y]["영업이익"]   = op
                if pd.isna(master[y]["당기순이익"]) and pd.notna(np_v):  master[y]["당기순이익"] = np_v
                if pd.isna(master[y]["EV/EBITDA"])  and pd.notna(ev) and ev > 0:
                    master[y]["EV/EBITDA"] = ev
    except:
        pass

    rows = [{"Year": y, **master[y]} for y in target_years]
    return pd.DataFrame(rows)

# ── User estimates (valuation tab) ─────────────────────────────────────────────
@ttl_cache(seconds=300)
def load_user_estimates() -> dict:
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ESTIMATES_FILE}?ref={GITHUB_BRANCH}"
        res = requests.get(url, headers=_gh_headers(), timeout=7)
        if res.status_code == 200:
            return json.loads(base64.b64decode(res.json()["content"]).decode())
    except:
        pass
    return {}

# ── Valuation calculations ─────────────────────────────────────────────────────
def calc_target(fin_df, stocks: int, method: str, multiple: float, curr_price, year: int):
    if fin_df is None or stocks == 0 or not curr_price:
        return None, None
    col_p = COL_MAP.get(method, "영업이익")
    row   = fin_df[fin_df["Year"] == year]
    if row.empty:
        return None, None
    val = row[col_p].values[0]
    if pd.isna(val) or val <= 0:
        return None, None
    try:
        tp = (curr_price * (multiple / float(val))
              if "EBITDA" in method
              else float(val) * UNIT * multiple / stocks)
        return (tp, (tp / curr_price - 1) * 100) if tp > 0 else (None, None)
    except:
        return None, None

def calc_current_mult(fin_df, stocks: int, method: str, curr_price, year: int):
    if fin_df is None or not curr_price:
        return None
    col_p = COL_MAP.get(method, "영업이익")
    row   = fin_df[fin_df["Year"] == year]
    if row.empty:
        return None
    val = row[col_p].values[0]
    if pd.isna(val) or val <= 0:
        return None
    try:
        if "EBITDA" in method:
            return float(val)
        return (curr_price * stocks) / (float(val) * UNIT)
    except:
        return None

def extract_number(val) -> float:
    if pd.isna(val) or str(val).strip() == "":
        return 0.0
    s = str(val).replace(",", "").replace("✅", "").strip()
    m = re.search(r"-?\d+\.?\d*", s)
    return float(m.group()) if m else 0.0
