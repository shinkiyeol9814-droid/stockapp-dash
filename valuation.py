import streamlit as st
import streamlit.components.v1 as components
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import io
import re
import requests
import json
import os
import base64
import time
import plotly.graph_objects as go

# --- 설정 및 상수 ---
GITHUB_REPO = "shinkiyeol9814-droid/stockapp"
GITHUB_BRANCH = "main"
ESTIMATES_FILE = "data/valuation/user_estimates.json"
UNIT = 100_000_000

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}
API_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

@st.cache_data(ttl=300)
def load_user_estimates():
    try:
        github_token = st.secrets.get("GITHUB_TOKEN")
        if not github_token: return {}
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ESTIMATES_FILE}?ref={GITHUB_BRANCH}"
        headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
        res = requests.get(url, headers=headers, timeout=7)
        if res.status_code == 200:
            content = base64.b64decode(res.json()['content']).decode('utf-8')
            return json.loads(content)
        return {}
    except: return {}

def save_to_github(file_path, content, message):
    try:
        github_token = st.secrets.get("GITHUB_TOKEN")
        if not github_token: return False, "Streamlit Secrets에 GITHUB_TOKEN가 없습니다."
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
        res = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=7)
        sha = res.json().get('sha') if res.status_code == 200 else None
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "branch": GITHUB_BRANCH
        }
        if sha: payload["sha"] = sha
        put_res = requests.put(url, headers=headers, json=payload, timeout=7)
        if put_res.status_code in [200, 201]: return True, "성공"
        return False, put_res.text
    except Exception as e:
        return False, f"통신 에러: {str(e)}"

@st.cache_data(ttl=86400)
def get_ticker_listing():
    for _ in range(3):
        try:
            df = fdr.StockListing('KRX')
            if not df.empty and 'Name' in df.columns: return df
        except: pass
    try:
        url = 'http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
        res = requests.get(url, headers=HEADERS, timeout=10)
        df = pd.read_html(io.StringIO(res.text), header=0)[0]
        df = df.rename(columns={'회사명': 'Name', '종목코드': 'Code'})
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        return df
    except: return pd.DataFrame(columns=['Code', 'Name'])

def get_stocks_count(ticker_row, ticker):
    try:
        if 'Stocks' in ticker_row.columns:
            sc = pd.to_numeric(ticker_row['Stocks'].values[0], errors='coerce')
            if pd.notna(sc) and sc > 0: return sc
    except: pass
    try:
        url = f"https://m.stock.naver.com/api/stock/{ticker}/integration"
        res = requests.get(url, headers=API_HEADERS, timeout=5).json()
        return int(res['stockEndType']['totalInfo']['stockCount'])
    except: pass
    try:
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        res = requests.get(url, headers=API_HEADERS, timeout=5)
        match = re.search(r'상장주식수<.*?<em>([\d,]+)</em>', res.text, re.DOTALL)
        if match: return int(match.group(1).replace(',', ''))
    except: pass
    try:
        if 'Marcap' in ticker_row.columns and 'Close' in ticker_row.columns:
            marcap  = pd.to_numeric(ticker_row['Marcap'].values[0], errors='coerce')
            close_p = pd.to_numeric(ticker_row['Close'].values[0],  errors='coerce')
            if pd.notna(marcap) and pd.notna(close_p) and marcap > 0 and close_p > 0:
                return int(marcap / close_p)
    except: pass
    return 1

def get_stock_price_data(ticker, start_date, end_date):
    try: return fdr.DataReader(ticker, start_date, end_date)
    except: return pd.DataFrame()


# ────────────────────────────────────────────────────────────────────────────────
# 파서 / 헬퍼 함수
# ────────────────────────────────────────────────────────────────────────────────
def parse_fin_table(html):
    """IFRS 포함 재무제표 테이블 파싱 (cF1001 손익계산서, cF2001 재무상태표)"""
    try:
        dfs = pd.read_html(io.StringIO(html))
        for df in dfs:
            if 'IFRS' in " ".join([str(c) for c in df.columns]):
                df = df.copy()
                df.index = df.iloc[:, 0].astype(str).str.strip().str.replace(' ', '')
                date_cols = [c for c in df.columns if re.search(r'\d{4}', str(c))]
                return df[date_cols]
    except:
        pass
    return None


def get_val(df_parsed, row_pattern, col):
    """행 이름 패턴으로 값 추출"""
    for k in df_parsed.index:
        if re.search(row_pattern, str(k), re.I):
            try:
                val = df_parsed.loc[k, col]
                if isinstance(val, pd.Series):
                    val = val.dropna()
                    val = val.iloc[0] if len(val) > 0 else np.nan
                cleaned = re.sub(r'[^\d\.-]', '', str(val))
                if cleaned and cleaned not in ['-', '.', '-.']:
                    return float(cleaned)
            except:
                pass
    return np.nan


def parse_consensus_value(s):
    """'38,872.9' → 38872.9 / '-50.90' → -50.90 / 'N/A' → np.nan"""
    if s is None:
        return np.nan
    s_clean = str(s).replace(',', '').strip()
    if s_clean in ['', '-', 'N/A', 'n/a']:
        return np.nan
    try:
        return float(s_clean)
    except:
        return np.nan


def fetch_consensus_data(ticker):
    """c1050001_data.aspx flag=2 호출 — 컨센서스 + 실적 통합 JSON"""
    try:
        today_str = datetime.today().strftime('%Y%m%d')
        url = (
            f"https://comp.wisereport.co.kr/company/ajax/c1050001_data.aspx"
            f"?flag=2&cmp_cd={ticker}&finGubun=MAIN&frq=0&sDT={today_str}&chartType=svg"
        )
        headers = HEADERS.copy()
        headers["Referer"] = f"https://comp.wisereport.co.kr/company/c1050001.aspx?cmp_cd={ticker}"
        res = requests.get(url, headers=headers, timeout=7)
        if res.status_code != 200:
            return None
        return res.json().get('JsonData', [])
    except:
        return None


@st.cache_data(ttl=3600)
def get_hybrid_financials(ticker):
    target_years = [2021, 2022, 2023, 2024, 2025, 2026, 2027]
    master_dict = {
        y: {'매출액': np.nan, '영업이익': np.nan, '당기순이익': np.nan,
            '자본총계': np.nan, 'EV/EBITDA': np.nan}
        for y in target_years
    }
    try:
        main_url = f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={ticker}"
        main_res = requests.get(main_url, headers=HEADERS, timeout=7)
        encparam = ""
        match = re.search(r"encparam\s*:\s*'([^']+)'", main_res.text)
        if match:
            encparam = match.group(1)
        ajax_headers = HEADERS.copy()
        ajax_headers["Referer"] = main_url

        # ── ① cF1001(손익계산서) + cF2001(재무상태표) ──
        fin_urls = [
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF2001.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
        ]
        for url in fin_urls:
            res = requests.get(url, headers=ajax_headers, timeout=7)
            df_parsed = parse_fin_table(res.text)
            if df_parsed is None:
                continue
            for c in df_parsed.columns:
                m = re.search(r'(20\d{2})', str(c))
                if not m:
                    continue
                y = int(m.group(1))
                if y not in target_years:
                    continue
                r   = get_val(df_parsed, r'^(매출액|영업수익)', c)
                o   = get_val(df_parsed, r'^영업이익$', c)
                if pd.isna(o): o = get_val(df_parsed, r'^영업이익\(발표기준\)', c)
                n   = get_val(df_parsed, r'^(당기순이익|지배주주순이익)', c)
                cap = get_val(df_parsed, r'^(자본총계|지배주주지분)', c)
                if pd.isna(master_dict[y]['매출액'])     and pd.notna(r):   master_dict[y]['매출액']   = r
                if pd.isna(master_dict[y]['영업이익'])   and pd.notna(o):   master_dict[y]['영업이익'] = o
                if pd.isna(master_dict[y]['당기순이익']) and pd.notna(n):   master_dict[y]['당기순이익'] = n
                if pd.isna(master_dict[y]['자본총계'])   and pd.notna(cap): master_dict[y]['자본총계'] = cap

        # ── ② 컨센서스 API (c1050001_data.aspx?flag=2) ──
        consensus_rows = fetch_consensus_data(ticker)
        if consensus_rows:
            for row_json in consensus_rows:
                ym = row_json.get('YYMM', '')
                m = re.search(r'(20\d{2})', ym)
                if not m:
                    continue
                y = int(m.group(1))
                if y not in target_years:
                    continue
                sales = parse_consensus_value(row_json.get('SALES'))
                op    = parse_consensus_value(row_json.get('OP'))
                np_v  = parse_consensus_value(row_json.get('NP'))
                ev    = parse_consensus_value(row_json.get('EV'))
                if pd.isna(master_dict[y]['매출액'])     and pd.notna(sales): master_dict[y]['매출액']     = sales
                if pd.isna(master_dict[y]['영업이익'])   and pd.notna(op):    master_dict[y]['영업이익']   = op
                if pd.isna(master_dict[y]['당기순이익']) and pd.notna(np_v):  master_dict[y]['당기순이익'] = np_v
                if pd.isna(master_dict[y]['EV/EBITDA']) and pd.notna(ev) and ev > 0:
                    master_dict[y]['EV/EBITDA'] = ev

    except:
        pass

    rows = []
    for y in target_years:
        row = master_dict[y].copy()
        row['Year']      = y
        row['Plot_Date'] = pd.to_datetime(f"{y}-12-28")
        row['Label']     = f"{y}년"
        rows.append(row)
    return pd.DataFrame(rows)


def make_card_ui(title, price_str, marcap_str, rate_str, is_up, is_zero=False):
    if is_zero: color, bg_color = "#888888", "#f4f4f4"
    else: color, bg_color = ("#ff4b4b" if is_up else "#0068c9"), ("#ff4b4b15" if is_up else "#0068c915")
    return f"""
    <div style="background-color: #ffffff; padding: 12px; border-radius: 8px; border: 1px solid #e0e0e0; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 10px;">
        <div style="font-size: 13px; color: #777; font-weight: 600; margin-bottom: 4px;">{title}</div>
        <div style="font-size: 22px; font-weight: 900; color: #222; margin-bottom: 2px;">{price_str}</div>
        <div style="font-size: 12px; color: #888; margin-bottom: 10px;">시총: {marcap_str}</div>
        <div style="display: inline-block; font-size: 14px; font-weight: 800; color: {color}; background-color: {bg_color}; padding: 4px 10px; border-radius: 6px;">{rate_str}</div>
    </div>
    """

def extract_number_or_nan(val):
    if pd.isna(val) or str(val).strip() == "": return np.nan
    s = str(val).replace(',', '').replace('✅', '').strip()
    m = re.search(r'-?\d+\.?\d*', s)
    return float(m.group()) if m else np.nan

def extract_number(val):
    if pd.isna(val) or str(val).strip() == "": return 0.0
    s = str(val).replace(',', '').replace('✅', '').strip()
    m = re.search(r'-?\d+\.?\d*', s)
    return float(m.group()) if m else 0.0

def apply_search():
    new_name = st.session_state.get("ui_corp_name", "").strip()
    if new_name:
        st.session_state.active_corp_name = new_name
    new_val_type = st.session_state.get("ui_val_type", "POR(영업익)")
    prev_val_type = st.session_state.get("active_val_type", "POR(영업익)")
    st.session_state.active_val_type = new_val_type
    new_is_float = "PBR" in new_val_type or "EBITDA" in new_val_type
    prev_is_float = "PBR" in prev_val_type or "EBITDA" in prev_val_type
    type_changed = new_is_float != prev_is_float
    if new_is_float:
        if type_changed: st.session_state.active_target_mult = 1.0
        else: st.session_state.active_target_mult = float(st.session_state.get("ui_target_mult_float", 1.0))
    else:
        if type_changed: st.session_state.active_target_mult = 10.0
        else: st.session_state.active_target_mult = float(int(st.session_state.get("ui_target_mult_int", 10)))

def render_valuation_menu():
    # 모바일에서 자리 비움 후 복귀 시 자동 리로드 (2분 이상 부재 → 새 데이터로 갱신)
    components.html("""
    <script>
    (function() {
        var AWAY_MS = 2 * 60 * 1000;
        var win = window.parent || window;
        var doc = win.document;
        // parent window에 상태 저장 → Streamlit 재렌더링(autorefresh)에도 유지됨
        if (!win.__valVisRegistered__) {
            win.__valVisRegistered__ = true;
            win.__valHiddenAt__ = null;
            doc.addEventListener('visibilitychange', function() {
                if (doc.hidden) {
                    win.__valHiddenAt__ = Date.now();
                } else if (win.__valHiddenAt__) {
                    if (Date.now() - win.__valHiddenAt__ > AWAY_MS) {
                        win.__valVisRegistered__ = false;
                        win.location.reload();
                    }
                    win.__valHiddenAt__ = null;
                }
            });
        }
    })();
    </script>
    """, height=0)

    if 'app_init_done' not in st.session_state:
        st.session_state.app_init_done = True
        q_code = st.query_params.get("stock_code", "")
        q_val  = st.query_params.get("val_type", "")
        q_mult = st.query_params.get("mult", "")
        if q_code:
            listing = get_ticker_listing()
            matched = listing[listing['Code'] == str(q_code).zfill(6)]
            if not matched.empty:
                restored_name = matched['Name'].values[0]
                st.session_state.active_corp_name = restored_name
                st.session_state.ui_corp_name     = restored_name
                if q_val:
                    st.session_state.active_val_type = q_val
                    st.session_state.ui_val_type     = q_val
                if q_mult:
                    mult = float(q_mult)
                    st.session_state.active_target_mult = mult
                    if q_val and ("PBR" in q_val or "EBITDA" in q_val):
                        st.session_state.ui_target_mult_float = mult
                        st.session_state.ui_target_mult_int   = 10
                    else:
                        st.session_state.ui_target_mult_int   = int(mult)
                        st.session_state.ui_target_mult_float = 1.0

    if 'active_corp_name'   not in st.session_state: st.session_state.active_corp_name   = ""
    if 'active_val_type'    not in st.session_state: st.session_state.active_val_type    = "POR(영업익)"
    if 'active_target_mult' not in st.session_state: st.session_state.active_target_mult = 10.0

    is_float_type = "PBR" in st.session_state.active_val_type or "EBITDA" in st.session_state.active_val_type
    prev_is_float = st.session_state.get('_prev_is_float', is_float_type)
    if is_float_type != prev_is_float:
        st.session_state.pop('ui_target_mult_float', None)
        st.session_state.pop('ui_target_mult_int', None)
        st.session_state.active_target_mult = 1.0 if is_float_type else 10.0
    st.session_state['_prev_is_float'] = is_float_type
    if is_float_type and 'ui_target_mult_float' not in st.session_state:
        st.session_state['ui_target_mult_float'] = float(st.session_state.active_target_mult)
    elif not is_float_type and 'ui_target_mult_int' not in st.session_state:
        st.session_state['ui_target_mult_int'] = int(st.session_state.active_target_mult)
    if 'ui_val_type' not in st.session_state:
        st.session_state['ui_val_type'] = st.session_state.active_val_type

    st.markdown("""
        <style>
        .stButton > button, [data-testid="stFormSubmitButton"] > button { background-color: #ffe6e6 !important; border-color: #ffcccc !important; }
        .stButton > button p, [data-testid="stFormSubmitButton"] > button p { color: #d63031 !important; font-weight: 600 !important; }
        .stButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover { background-color: #ffcccc !important; }
        </style>
    """, unsafe_allow_html=True)
    st.markdown("<div class='main-title'>📈 가치평가 시뮬레이터</div>", unsafe_allow_html=True)

    val_options = ["PER(순이익)", "POR(영업익)", "PBR(자본총계)", "EV/EBITDA"]

    with st.form("search_form", border=False):
        col1, col2, col3, col4 = st.columns([2, 1.5, 1.2, 1])
        with col1:
            st.text_input("종목명", key="ui_corp_name", placeholder="예: 삼성전자")
        with col2:
            idx = val_options.index(st.session_state.ui_val_type) if st.session_state.ui_val_type in val_options else 1
            st.selectbox("평가방식", val_options, index=idx, key="ui_val_type")
        with col3:
            if is_float_type: st.number_input("목표배수", step=0.1, format="%.1f", key="ui_target_mult_float")
            else: st.number_input("목표배수", step=1, format="%d", key="ui_target_mult_int")
        with col4:
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
            submitted = st.form_submit_button("갱신", type="primary", use_container_width=True)

    if submitted:
        apply_search()
        st.rerun()

    if "EBITDA" in st.session_state.active_val_type:
        st.caption("💡 **[EV/EBITDA 안내]** EV/EBITDA는 배수(ratio) 데이터만 수집 가능하므로 주가 밴드 차트 대신 **배수 추이 차트**를 표시합니다. 적자 연도(음수 배수)는 자동 제외됩니다.")

    corp_name    = st.session_state.active_corp_name
    val_type     = st.session_state.active_val_type
    target_mult  = float(st.session_state.active_target_mult)
    display_mult_str = f"{target_mult:.1f}" if ("PBR" in val_type or "EBITDA" in val_type) else f"{int(target_mult)}"
    cols_to_edit = ['매출액', '영업이익', '당기순이익', '자본총계', 'EV/EBITDA']

    if corp_name:
        listing = get_ticker_listing()
        clean_target = corp_name.replace(" ", "").upper()
        ticker_row = listing[listing['Name'].astype(str).str.replace(" ", "").str.upper() == clean_target]
        if ticker_row.empty and corp_name.isdigit():
            ticker_row = listing[listing['Code'].astype(str).str.endswith(corp_name)]
        if not ticker_row.empty:
            ticker = str(ticker_row['Code'].values[0]).split('.')[0].strip().zfill(6)
            st.query_params["stock_code"] = ticker
            st.query_params["val_type"]   = val_type
            st.query_params["mult"]       = str(target_mult)

        with st.spinner("데이터 분석 중..."):
            if ticker_row.empty:
                st.error("❌ 종목을 찾을 수 없습니다. 종목명을 정확히 입력해주세요.")
            else:
                ticker = str(ticker_row['Code'].values[0]).split('.')[0].strip().zfill(6)
                stocks_count = get_stocks_count(ticker_row, ticker)
                fin_df = get_hybrid_financials(ticker)
                orig_fin_df = fin_df.copy()
                user_estimates = load_user_estimates()
                ticker_estimates = user_estimates.get(ticker, {})

                manual_indices = []
                for idx, row in fin_df.iterrows():
                    yr = str(row['Year'])
                    if yr in ticker_estimates:
                        for col in cols_to_edit:
                            if pd.isna(row[col]) or row[col] == 0:
                                if col in ticker_estimates[yr]:
                                    fin_df.at[idx, col] = float(ticker_estimates[yr][col])
                                    manual_indices.append((idx, col))

                df_price = get_stock_price_data(ticker, "2021-01-01", datetime.today().strftime('%Y-%m-%d'))
                if not df_price.empty:
                    curr_p      = df_price.iloc[-1]['Close']
                    prev_p      = df_price.iloc[-2]['Close'] if len(df_price) > 1 else curr_p
                    curr_marcap = (curr_p * stocks_count) / UNIT
                    updown      = ((curr_p / prev_p) - 1) * 100
                    st.markdown(f"<div class='sub-header'>📊 {corp_name} ({ticker})</div>", unsafe_allow_html=True)

                    with st.form(f"fin_form_{ticker}"):
                        st.markdown("<div class='sub-header' style='margin-top:10px; font-size:15px !important;'>📝 연도별 재무 상세 <span style='color:red; font-size:12px; font-weight:normal;'>(※ 값 수정 후 [갱신] 클릭 시 재측정)</span></div>", unsafe_allow_html=True)
                        display_df = fin_df[['Label'] + cols_to_edit].copy()
                        for col in cols_to_edit:
                            if col == 'EV/EBITDA':
                                display_df[col] = display_df[col].apply(lambda x: "" if pd.isna(x) or x == 0 else f"{float(x):.1f}")
                            else:
                                display_df[col] = display_df[col].apply(lambda x: "" if pd.isna(x) or x == 0 else f"{int(x):,}")
                        for r, c in manual_indices:
                            val = fin_df.at[r, c]
                            if pd.notna(val) and val != 0:
                                if c == 'EV/EBITDA': display_df.at[r, c] = f"{float(val):.1f} ✅"
                                else: display_df.at[r, c] = f"{int(val):,} ✅"
                        edited_df = st.data_editor(display_df, disabled=["Label"], hide_index=True, use_container_width=True, key=f"editor_{ticker}")
                        btn_col1, btn_col2 = st.columns(2)
                        with btn_col1: fin_update_clicked = st.form_submit_button("갱신", type="primary", use_container_width=True)
                        with btn_col2: fin_save_clicked = st.form_submit_button("저장", type="secondary", use_container_width=True)

                    if fin_update_clicked:
                        st.success("✅ 화면에 수치가 갱신되었습니다. (영구 보존하려면 '저장'을 누르세요)")
                    if fin_save_clicked:
                        with st.spinner("GitHub에 저장 중..."):
                            new_estimates = load_user_estimates()
                            if ticker not in new_estimates: new_estimates[ticker] = {}
                            for idx, row in edited_df.iterrows():
                                yr = str(orig_fin_df.at[idx, 'Year'])
                                if yr not in new_estimates[ticker]: new_estimates[ticker][yr] = {}
                                for col in cols_to_edit:
                                    orig_val   = orig_fin_df.at[idx, col]
                                    edited_val = extract_number(row[col])
                                    if pd.isna(orig_val) or orig_val == 0:
                                        if pd.notna(edited_val) and edited_val != 0: new_estimates[ticker][yr][col] = float(edited_val)
                                        else: new_estimates[ticker][yr].pop(col, None)
                                    else: new_estimates[ticker][yr].pop(col, None)
                            empty_years = [y for y, data in new_estimates[ticker].items() if not data]
                            for y in empty_years: del new_estimates[ticker][y]
                            if not new_estimates[ticker]: del new_estimates[ticker]
                            success, msg = save_to_github(ESTIMATES_FILE, json.dumps(new_estimates, indent=4, ensure_ascii=False), f"Update {corp_name} estimates")
                            if success:
                                st.success("✅ 추정치가 성공적으로 저장되었습니다! 화면을 즉시 갱신합니다.")
                                get_hybrid_financials.clear()
                                load_user_estimates.clear()
                                time.sleep(0.7)
                                st.rerun()
                            else: st.error(f"❌ 저장 실패: {msg}")

                    if fin_update_clicked or fin_save_clicked:
                        for col in cols_to_edit:
                            fin_df[col] = edited_df[col].apply(extract_number_or_nan).values

                    col_p     = '당기순이익'
                    band_name = "PER"
                    if "POR" in val_type:      col_p = '영업이익';  band_name = "POR"
                    elif "PBR" in val_type:    col_p = '자본총계';  band_name = "PBR"
                    elif "EBITDA" in val_type: col_p = 'EV/EBITDA'; band_name = "EV/EBITDA"

                    def get_t(y):
                        row = fin_df[fin_df['Year'] == y]
                        if len(row) > 0 and pd.notna(row[col_p].values[0]) and row[col_p].values[0] > 0:
                            if "EBITDA" in val_type:
                                curr_mult = float(row[col_p].values[0])
                                target_marcap = curr_marcap * (target_mult / curr_mult)
                                tp = curr_p * (target_mult / curr_mult)
                                return float(tp), float(((tp / curr_p) - 1) * 100), float(target_marcap)
                            else:
                                val = float(row[col_p].values[0]) * UNIT
                                target_marcap = val * target_mult
                                tp = float(target_marcap / stocks_count) if stocks_count > 0 else 0
                                return float(tp), float(((tp / curr_p) - 1) * 100), float(target_marcap / UNIT)
                        return 0.0, 0.0, 0.0

                    y1, y2        = datetime.today().year, datetime.today().year + 1
                    tp1, up1, tm1 = get_t(y1)
                    tp2, up2, tm2 = get_t(y2)
                    last_date_str = df_price.index[-1].strftime('%m.%d')

                    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        rate_str = f"{updown:+.2f}%"
                        st.markdown(make_card_ui(f"현재가 ({last_date_str})", f"{curr_p:,.0f}원", f"{curr_marcap:,.0f}억", rate_str, updown > 0, is_zero=(updown == 0)), unsafe_allow_html=True)
                    with col2:
                        if tp1 > 0: st.markdown(make_card_ui(f"목표가 ({str(y1)[-2:]}년)", f"{tp1:,.0f}원", f"{tm1:,.0f}억", f"목표대비 {up1:+.1f}%", up1 > 0), unsafe_allow_html=True)
                        elif tp1 <= 0 and up1 == -100.0: st.markdown(make_card_ui(f"목표가 ({str(y1)[-2:]}년)", "0원", f"{tm1:,.0f}억", "과차입(가치없음)", False, is_zero=False), unsafe_allow_html=True)
                        else: st.markdown(make_card_ui(f"목표가 ({str(y1)[-2:]}년)", "N/A", "-", "데이터 없음", False, is_zero=True), unsafe_allow_html=True)
                    with col3:
                        if tp2 > 0: st.markdown(make_card_ui(f"목표가 ({str(y2)[-2:]}년)", f"{tp2:,.0f}원", f"{tm2:,.0f}억", f"목표대비 {up2:+.1f}%", up2 > 0), unsafe_allow_html=True)
                        elif tp2 <= 0 and up2 == -100.0: st.markdown(make_card_ui(f"목표가 ({str(y2)[-2:]}년)", "0원", f"{tm2:,.0f}억", "과차입(가치없음)", False, is_zero=False), unsafe_allow_html=True)
                        else: st.markdown(make_card_ui(f"목표가 ({str(y2)[-2:]}년)", "N/A", "-", "데이터 없음", False, is_zero=True), unsafe_allow_html=True)

                    st.markdown("<div class='sub-header' style='margin-top:20px;'>📉 밸류에이션 차트</div>", unsafe_allow_html=True)
                    chart_period = st.radio("조회 기간 설정", ["1년", "2년", "3년", "5년", "전체"], index=4, horizontal=True, label_visibility="collapsed", key="chart_period_radio")
                    end_date_dt = df_price.index[-1]
                    if chart_period == "1년":   start_date_chart = end_date_dt - pd.DateOffset(years=1)
                    elif chart_period == "2년": start_date_chart = end_date_dt - pd.DateOffset(years=2)
                    elif chart_period == "3년": start_date_chart = end_date_dt - pd.DateOffset(years=3)
                    elif chart_period == "5년": start_date_chart = end_date_dt - pd.DateOffset(years=5)
                    else: start_date_chart = pd.to_datetime("2021-01-01")

                    future_dates   = pd.date_range(start=df_price.index[-1], end=pd.to_datetime('2028-02-28'), freq='D')
                    extended_dates = df_price.index.append(future_dates[1:])

                    raw_metrics   = pd.to_numeric(fin_df[col_p], errors='coerce').values
                    cur_metrics   = pd.Series(raw_metrics).ffill().bfill().values
                    if not "EBITDA" in val_type:
                        cur_metrics = cur_metrics * UNIT
                    cur_metrics   = np.nan_to_num(cur_metrics, nan=0.1)
                    cur_metrics   = np.where(cur_metrics <= 0, 0.1, cur_metrics)
                    band_dates_ts = fin_df['Plot_Date'].map(datetime.timestamp).values
                    ext_interp    = np.interp(extended_dates.map(datetime.timestamp).values, band_dates_ts, cur_metrics)

                    today_marcap     = curr_p * stocks_count
                    today_metric_val = ext_interp[len(df_price) - 1]

                    if "EBITDA" in val_type:
                        today_m = float(today_metric_val) if today_metric_val > 0 else 0
                    else:
                        today_m = float(today_marcap / today_metric_val) if today_metric_val > 0 else 0

                    date_mask       = (df_price.index >= start_date_chart) & (df_price.index <= end_date_dt)
                    interp_history  = ext_interp[:len(df_price)]
                    hist_marcap     = df_price['Close'].values * stocks_count

                    if "EBITDA" in val_type:
                        all_daily_val = np.where(interp_history > 0, interp_history, np.nan)
                    else:
                        all_daily_val = np.where(interp_history > 0, hist_marcap / interp_history, np.nan)

                    valid_mask      = date_mask & (interp_history > 0)
                    valid_hist_mult = all_daily_val[valid_mask]
                    valid_hist_mult = valid_hist_mult[~np.isnan(valid_hist_mult)]

                    bands     = []
                    avg_m_val = 0
                    if len(valid_hist_mult) > 0:
                        realistic_mults = valid_hist_mult[(valid_hist_mult > 0) & (valid_hist_mult < 300)]
                        if len(realistic_mults) > 0:
                            q_min         = np.percentile(realistic_mults, 5)
                            q_max         = np.percentile(realistic_mults, 95)
                            filtered_hist = realistic_mults[(realistic_mults >= q_min) & (realistic_mults <= q_max)]
                            if len(filtered_hist) > 0:
                                avg_m_val     = np.mean(filtered_hist)
                                mn, mx        = np.min(filtered_hist), np.max(filtered_hist)
                                fallback_step = 1.0 if ("PBR" in val_type or "EBITDA" in val_type) else 5.0
                                if mx <= mn: mx = mn + fallback_step
                                stp   = (mx - mn) / 3
                                bands = sorted(list(set([round(mn + (stp * i), 1) for i in range(4) if mn + (stp * i) > 0])))

                    target_year_end = pd.to_datetime(f"{fin_df['Year'].max()}-12-31")
                    x_range         = [start_date_chart, target_year_end]
                    cols            = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd']

                    def _fmt_metric(v):
                        try:
                            f = float(v)
                            return "N/A" if (np.isnan(f) or f == 0) else (f"{f:,.1f}x" if "EBITDA" in val_type else f"{f:,.0f}억")
                        except: return "N/A"

                    # ────────────────────────────────────────────────────────────
                    # EV/EBITDA 모드: 주가 밴드(fig1) 건너뛰고 배수 추이만 표시
                    # PER/POR/PBR 모드: 기존 fig1 + fig2 모두 표시
                    # ────────────────────────────────────────────────────────────
                    if "EBITDA" not in val_type:
                        # ── fig1: 주가 밴드 차트 (PER/POR/PBR 전용) ──
                        def get_band_y(m_val):
                            return np.where(ext_interp > 0, (ext_interp * float(m_val)) / stocks_count, np.nan)

                        fig1 = go.Figure()
                        mask_future        = (extended_dates >= start_date_chart) & (extended_dates <= target_year_end)
                        visible_ext_interp = ext_interp[mask_future]
                        df_filtered_price  = df_price[(df_price.index >= start_date_chart) & (df_price.index <= end_date_dt)]
                        price_max = df_filtered_price['Close'].max() if not df_filtered_price.empty else curr_p
                        price_min = df_filtered_price['Close'].min() if not df_filtered_price.empty else curr_p

                        important_vals_fig1 = []
                        if len(visible_ext_interp) > 0:
                            important_vals_fig1.extend(pd.Series(get_band_y(target_mult)[mask_future]).dropna().tolist())
                            if avg_m_val > 0: important_vals_fig1.extend(pd.Series(get_band_y(avg_m_val)[mask_future]).dropna().tolist())
                            if today_m > 0 and today_m < 300: important_vals_fig1.extend(pd.Series(get_band_y(today_m)[mask_future]).dropna().tolist())

                        core_max = max([price_max] + [v for v in important_vals_fig1 if pd.notna(v) and v > 0])
                        core_min = min([price_min] + [v for v in important_vals_fig1 if pd.notna(v) and v > 0])
                        y_max = max(core_max * 1.2, price_max * 1.5)
                        y_min = core_min * 0.8
                        fig1.update_yaxes(range=[max(0, y_min), y_max])

                        fig1.add_trace(go.Scatter(x=df_price.index, y=df_price['Close'], mode='lines', name='주가', line=dict(color='var(--text-color)', width=1.5)))
                        for i, b in enumerate(bands):
                            if pd.notna(b): fig1.add_trace(go.Scatter(x=extended_dates, y=get_band_y(b), mode='lines', name=f'{b}x', line=dict(color=cols[i % 4], width=1, dash='dot')))
                        fig1.add_trace(go.Scatter(x=extended_dates, y=get_band_y(target_mult), mode='lines', name=f'<b>목표Val ({display_mult_str}x)</b>', line=dict(color='blue', width=1.5)))
                        if avg_m_val > 0: fig1.add_trace(go.Scatter(x=extended_dates, y=get_band_y(avg_m_val), mode='lines', name=f'<b>AvgVal ({avg_m_val:.1f}x)</b>', line=dict(color='green', width=1.5)))
                        if today_m > 0 and today_m < 300: fig1.add_trace(go.Scatter(x=extended_dates, y=get_band_y(today_m), mode='lines', name=f'<b>현재Val ({today_m:.1f}x)</b>', line=dict(color='red', width=1.5)))

                        fig1.update_xaxes(range=x_range, tickmode='array', tickvals=fin_df['Plot_Date'], ticktext=[f"{str(y)[-2:]}년" for y in fin_df['Year']], showticklabels=True)
                        fig1.update_layout(height=400, margin=dict(l=0, r=20, t=70, b=10), title=dict(text=f"[{band_name} 밴드]", x=0.0, y=0.99, font=dict(size=14)), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=11)), hovermode="x unified", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig1, use_container_width=True, config={'staticPlot': True})
                        st.write("")

                    # ── fig2: 배수 추이 차트 (모든 평가방식 공통) ──
                    fig2 = go.Figure()
                    important_mults = [target_mult]
                    if avg_m_val > 0: important_mults.append(avg_m_val)
                    if today_m > 0 and today_m < 300: important_mults.append(today_m)
                    for b in bands:
                        if pd.notna(b) and b <= (avg_m_val * 2 if avg_m_val > 0 else 50): important_mults.append(float(b))

                    core_m_max = max(important_mults) if important_mults else 20
                    y2_max     = core_m_max * 1.6
                    fig2.update_yaxes(range=[0, y2_max])
                    fig2.add_trace(go.Scatter(x=df_price.index, y=all_daily_val[:len(df_price)], mode='lines', name='당일Val', line=dict(color='var(--text-color)', width=1.5)))

                    x_start, x_end = df_price.index[0], extended_dates[-1]
                    for i, b in enumerate(bands):
                        if pd.notna(b):
                            fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[float(b), float(b)], mode='lines', name=f'{b}x', line=dict(color=cols[i % 4], width=1, dash='dash')))
                            fig2.add_annotation(x=extended_dates[-1] + timedelta(days=2), y=float(b), text=f"{b}x", showarrow=False, xanchor="left", yanchor="middle", font=dict(size=11, color=cols[i % 4], weight="bold"))

                    fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[target_mult, target_mult], mode='lines', name=f'<b>목표Val ({display_mult_str}x)</b>', line=dict(color='blue', width=1.5)))
                    fig2.add_annotation(x=extended_dates[-1] + timedelta(days=2), y=target_mult, text=f"목표: {display_mult_str}x", showarrow=False, xanchor="left", yanchor="middle", font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(0,0,255,0.8)", bordercolor="blue", borderpad=3, borderwidth=1)

                    if avg_m_val > 0:
                        fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[avg_m_val, avg_m_val], mode='lines', name=f'<b>AvgVal ({avg_m_val:.1f}x)</b>', line=dict(color='green', width=2)))
                        fig2.add_annotation(x=extended_dates[-1] + timedelta(days=2), y=avg_m_val, text=f"Avg: {avg_m_val:.1f}x", showarrow=False, xanchor="left", yanchor="middle", font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(0,128,0,0.8)", bordercolor="green", borderpad=3, borderwidth=1)
                        mid_date = x_start + (x_end - x_start) * 0.6
                        fig2.add_annotation(x=mid_date, y=avg_m_val, text=f"{avg_m_val:.1f}x", showarrow=False, xanchor="center", yanchor="bottom", yshift=4, font=dict(size=13, color="green", weight="bold"))

                    if today_m > 0 and today_m < 300:
                        fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[today_m, today_m], mode='lines', name=f'<b>현재Val ({today_m:.1f}x)</b>', line=dict(color='red', width=1.5)))
                        fig2.add_annotation(x=extended_dates[-1] + timedelta(days=2), y=today_m, text=f"현재: {today_m:.1f}x", showarrow=False, xanchor="left", yanchor="middle", font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(255,0,0,0.8)", bordercolor="red", borderpad=3, borderwidth=1)

                    bottom_x_labels = [f"{str(row['Year'])[-2:]}년<br>{_fmt_metric(row[col_p])}" for _, row in fin_df.iterrows()]
                    fig2.update_xaxes(range=x_range, tickmode='array', tickvals=fin_df['Plot_Date'], ticktext=bottom_x_labels, showticklabels=True)

                    # EV/EBITDA 모드에서는 이게 유일한 차트 → 높이 키움
                    fig2_height = 400 if "EBITDA" in val_type else 300
                    fig2_title  = f"[EV/EBITDA 배수 추이]" if "EBITDA" in val_type else f"[평균 {band_name} 밴드]"
                    fig2.update_layout(height=fig2_height, margin=dict(l=0, r=20, t=50, b=80), title=dict(text=fig2_title, x=0.0, y=0.99, font=dict(size=14)), showlegend=("EBITDA" in val_type), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=11)), hovermode="x unified", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig2, use_container_width=True, config={'staticPlot': True})

                else: st.error("❌ 주가 데이터를 불러오는 데 실패했습니다. 종목명을 확인하거나 잠시 후 다시 시도해주세요.")
    else: st.info("👆 상단에 종목명을 입력하고 갱신 버튼을 눌러주세요!")
    st.markdown("<div style='height: 50px;'></div>", unsafe_allow_html=True)
