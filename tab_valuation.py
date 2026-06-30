"""
tab_valuation.py — 가치평가 시뮬레이터 tab.
"""
import json
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import html, dcc, Input, Output, State, callback, ctx, no_update, ALL
from datetime import datetime

from data_layer import (
    get_ticker_listing, get_stocks_count, get_stock_price_data,
    get_hybrid_financials, load_user_estimates, save_to_github,
    UNIT, ESTIMATES_FILE, extract_number,
)

VAL_OPTIONS = ["POR(영업익)", "PER(순이익)", "PBR(자본총계)", "EV/EBITDA"]
COL_MAP     = {
    "POR(영업익)":   "영업이익",
    "PER(순이익)":   "당기순이익",
    "PBR(자본총계)": "자본총계",
    "EV/EBITDA":     "EV/EBITDA",
}
COLS_TO_EDIT = ["매출액", "영업이익", "당기순이익", "자본총계", "EV/EBITDA"]

FIN_COL_DEFS = [
    {"field": "Label",    "headerName": "연도",   "width": 90, "pinned": "left"},
    {"field": "매출액",   "headerName": "매출액(억)", "width": 130, "editable": True,
     "type": "numericColumn", "cellEditor": "agNumberCellEditor"},
    {"field": "영업이익", "headerName": "영업이익(억)", "width": 130, "editable": True,
     "type": "numericColumn", "cellEditor": "agNumberCellEditor"},
    {"field": "당기순이익", "headerName": "당기순이익(억)", "width": 140, "editable": True,
     "type": "numericColumn", "cellEditor": "agNumberCellEditor"},
    {"field": "자본총계", "headerName": "자본총계(억)", "width": 130, "editable": True,
     "type": "numericColumn", "cellEditor": "agNumberCellEditor"},
    {"field": "EV/EBITDA", "headerName": "EV/EBITDA(x)", "width": 130, "editable": True,
     "type": "numericColumn", "cellEditor": "agNumberCellEditor",
     "valueFormatter": {"function": "params.value ? params.value.toFixed(1) : '-'"}},
]

PERIOD_YEARS = {"1년": 1, "2년": 2, "3년": 3, "5년": 5, "전체": None}


# ── Layout ─────────────────────────────────────────────────────────────────────
def layout():
    return html.Div([
        dcc.Store(id="val-ticker-info"),  # {ticker, name, stocks}
        dcc.Store(id="val-fin-data"),     # serialized fin_df
        dcc.Store(id="val-price-data"),   # serialized price history

        html.H5("📈 가치평가 시뮬레이터", className="mb-3"),

        # Search form
        dbc.Row([
            dbc.Col(
                dbc.Input(id="val-corp-input", placeholder="종목명 (예: 삼성전자)",
                          size="sm", debounce=False),
                width=3,
            ),
            dbc.Col(
                dbc.Select(
                    id="val-type-select",
                    options=[{"label": v, "value": v} for v in VAL_OPTIONS],
                    value="POR(영업익)",
                    size="sm",
                ),
                width=2,
            ),
            dbc.Col(
                dbc.InputGroup([
                    dbc.InputGroupText("배수"),
                    dbc.Input(id="val-mult-input", type="number", value=12,
                              min=0.1, max=300, step=0.5, size="sm"),
                ]),
                width=2,
            ),
            dbc.Col(
                dbc.Button("🔍 갱신", id="val-search-btn", color="primary",
                           size="sm", n_clicks=0),
                width="auto",
            ),
        ], className="mb-3 g-2 align-items-center"),

        html.Div(id="val-status", className="mb-2"),

        # Suggestion dropdown
        html.Div(id="val-suggestions", className="mb-2"),

        # Results (cards + financial table) — Loading spinner 표시
        dcc.Loading(
            html.Div(id="val-results"),
            type="circle",
            color="#0d6efd",
            style={"minHeight": "60px"},
        ),

        # Chart section — always in static layout so val-chart-period is always accessible
        html.Div([
            html.Hr(className="my-3"),
            dbc.Row([
                dbc.Col(html.H6("📉 밸류에이션 차트", className="mb-0"), width="auto"),
                dbc.Col(
                    dbc.RadioItems(
                        id="val-chart-period",
                        options=[{"label": l, "value": l}
                                 for l in ["1년", "2년", "3년", "5년", "전체"]],
                        value="전체",
                        inline=True,
                    ),
                    width="auto", className="ms-auto",
                ),
            ], className="mb-2 align-items-center"),
            dcc.Loading(
                html.Div(id="val-chart"),
                type="dot",
                color="#0d6efd",
            ),
        ], id="val-chart-section", style={"display": "none"}),
    ])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _card(title, price_str, marcap_str, rate_str, is_up, is_zero=False):
    color    = "#888" if is_zero else ("#C62828" if is_up else "#1565C0")
    bg_color = "#f5f5f5" if is_zero else ("#fff5f5" if is_up else "#f0f4ff")
    return dbc.Card(dbc.CardBody([
        html.Div(title, className="text-muted", style={"fontSize": "12px", "fontWeight": "600"}),
        html.Div(price_str, style={"fontSize": "22px", "fontWeight": "900", "color": "#222"}),
        html.Div(f"시총: {marcap_str}", className="text-muted", style={"fontSize": "11px"}),
        html.Span(rate_str, style={"color": color, "backgroundColor": bg_color,
                                    "padding": "3px 8px", "borderRadius": "6px",
                                    "fontWeight": "bold", "fontSize": "13px"}),
    ]), className="text-center", style={"boxShadow": "0 2px 4px rgba(0,0,0,.07)"})


def _build_chart(fin_df, df_price, stocks_count, curr_p, curr_marcap,
                 val_type: str, col_p: str, target_mult: float,
                 chart_period: str = "전체"):
    try:
        future_dates = pd.date_range(
            start=df_price.index[-1], end=pd.to_datetime("2028-02-28"), freq="D"
        )
        # pandas 2.x 호환: Index.append() 대신 list concat 사용
        extended_dates = pd.DatetimeIndex(
            df_price.index.tolist() + future_dates[1:].tolist()
        )

        raw_metrics = pd.to_numeric(fin_df[col_p], errors="coerce").values
        cur_metrics = pd.Series(raw_metrics).ffill().bfill().values
        if "EBITDA" not in val_type:
            cur_metrics = cur_metrics * UNIT
        cur_metrics = np.nan_to_num(cur_metrics, nan=0.1)
        cur_metrics = np.where(cur_metrics <= 0, 0.1, cur_metrics)

        def _ts(dti):
            return np.array([t.timestamp() for t in dti], dtype=float)

        band_dates_ts = _ts(pd.to_datetime([f"{y}-12-28" for y in fin_df["Year"]]))
        ext_ts        = _ts(extended_dates)
        ext_interp    = np.interp(ext_ts, band_dates_ts, cur_metrics)

        if "EBITDA" in val_type:
            hist_metric = np.where(ext_interp[:len(df_price)] > 0,
                                   ext_interp[:len(df_price)], np.nan)
        else:
            hist_marcap = df_price["Close"].values * stocks_count
            hist_metric = np.where(ext_interp[:len(df_price)] > 0,
                                   hist_marcap / ext_interp[:len(df_price)], np.nan)

        valid = hist_metric[~np.isnan(hist_metric)]
        valid = valid[(valid > 0) & (valid < 300)]
        bands, avg_m = [], 0.0
        if len(valid) > 0:
            q5, q95 = np.percentile(valid, 5), np.percentile(valid, 95)
            filt = valid[(valid >= q5) & (valid <= q95)]
            if len(filt) > 0:
                avg_m = float(np.mean(filt))
                mn, mx = float(np.min(filt)), float(np.max(filt))
                stp = (mx - mn) / 3 if mx > mn else 1.0
                bands = sorted(set([round(mn + stp * i, 1) for i in range(4) if mn + stp * i > 0]))

        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd"]

        if "EBITDA" in val_type:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_price.index, y=hist_metric,
                name=f"현재 {val_type}",
                line=dict(color="#333", width=1.5),
            ))
            for i, m in enumerate(bands):
                fig.add_hline(y=m, line_dash="dot", line_color=colors[i % len(colors)],
                              annotation_text=f"{m:.1f}x", annotation_position="right")
            fig.add_hline(y=target_mult, line_dash="solid", line_color="#FF0000",
                          annotation_text=f"목표 {target_mult:.1f}x", annotation_position="right",
                          line_width=2)
            fig.update_layout(yaxis_title=val_type, xaxis_title="",
                              margin=dict(l=0, r=80, t=20, b=0), height=380)
        else:
            def _band_y(m_val):
                return np.where(ext_interp > 0, ext_interp * float(m_val) / stocks_count, np.nan)

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_price.index, y=df_price["Close"],
                name="주가",
                line=dict(color="#333", width=1.5),
            ))
            for i, m in enumerate(bands):
                fig.add_trace(go.Scatter(
                    x=extended_dates, y=_band_y(m),
                    name=f"{val_type.split('(')[0]} {m:.0f}x",
                    line=dict(color=colors[i % len(colors)], dash="dot", width=1),
                ))
            fig.add_trace(go.Scatter(
                x=extended_dates, y=_band_y(target_mult),
                name=f"목표 {target_mult:.1f}x",
                line=dict(color="#FF0000", width=2),
            ))
            fig.update_layout(yaxis_title="주가 (원)", xaxis_title="",
                              margin=dict(l=0, r=20, t=20, b=0), height=380,
                              legend=dict(orientation="h", y=-0.15))

        # 차트 기간 필터 적용
        years = PERIOD_YEARS.get(chart_period or "전체")
        if years:
            x_start = pd.Timestamp.now() - pd.DateOffset(years=years)
            fig.update_xaxes(range=[x_start, extended_dates[-1]])

        return dcc.Graph(figure=fig, config={"displayModeBar": False})
    except Exception as e:
        return dbc.Alert(f"차트 생성 실패: {e}", color="warning")


# ── Callbacks ───────────────────────────────────────────────────────────────────

@callback(
    Output("val-suggestions", "children"),
    Input("val-corp-input", "value"),
    prevent_initial_call=True,
)
def suggest_stocks(q: str):
    if not q or len(q) < 1:
        return html.Div()
    listing  = get_ticker_listing()
    filtered = listing[listing["Name"].str.contains(q, case=False, na=False)].head(8)
    if filtered.empty:
        return html.Div()
    opts = [
        dbc.Button(
            f"{r['Name']} ({r['Code']})",
            id={"type": "val-suggestion", "index": r["Name"]},
            color="light", size="sm", className="me-1 mb-1",
            n_clicks=0,
        )
        for _, r in filtered.iterrows()
    ]
    return html.Div(opts)


@callback(
    Output("val-corp-input", "value"),
    Input({"type": "val-suggestion", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def pick_suggestion(n_clicks_list):
    triggered = ctx.triggered_id
    if triggered and isinstance(triggered, dict):
        return triggered["index"]
    return no_update


@callback(
    Output("val-results",       "children"),
    Output("val-chart",         "children"),
    Output("val-chart-section", "style"),
    Output("val-status",        "children"),
    Output("val-ticker-info",   "data"),
    Output("val-fin-data",      "data"),
    Output("val-price-data",    "data"),
    Input("val-search-btn",     "n_clicks"),
    State("val-corp-input",     "value"),
    State("val-type-select",    "value"),
    State("val-mult-input",     "value"),
    State("val-chart-period",   "value"),
    prevent_initial_call=True,
)
def search_and_render(n_clicks, corp_name: str, val_type: str, target_mult, chart_period):
    HIDDEN = {"display": "none"}
    SHOWN  = {"display": "block"}

    if not n_clicks or not corp_name:
        return (no_update,) * 7

    target_mult = float(target_mult or 12)
    listing     = get_ticker_listing()
    clean       = corp_name.replace(" ", "").upper()
    ticker_row  = listing[listing["Name"].astype(str).str.replace(" ", "").str.upper() == clean]

    if ticker_row.empty:
        return (
            html.Div(),
            no_update, HIDDEN,
            dbc.Alert(f"종목을 찾을 수 없습니다: {corp_name}", color="danger"),
            no_update, no_update, no_update,
        )

    ticker       = str(ticker_row["Code"].values[0]).zfill(6)
    stocks_count = get_stocks_count(ticker_row, ticker)
    fin_df       = get_hybrid_financials(ticker)

    # Merge user estimates
    user_est   = load_user_estimates()
    ticker_est = user_est.get(ticker, {})
    for idx, row in fin_df.iterrows():
        yr = str(row["Year"])
        if yr in ticker_est:
            for col in COLS_TO_EDIT:
                if (pd.isna(row[col]) or row[col] == 0) and col in ticker_est[yr]:
                    fin_df.at[idx, col] = float(ticker_est[yr][col])

    df_price = get_stock_price_data(ticker, "2021-01-01", datetime.today().strftime("%Y-%m-%d"))

    if df_price.empty:
        return (
            html.Div(),
            no_update, HIDDEN,
            dbc.Alert("주가 데이터를 불러올 수 없습니다.", color="warning"),
            no_update, no_update, no_update,
        )

    curr_p      = float(df_price.iloc[-1]["Close"])
    prev_p      = float(df_price.iloc[-2]["Close"]) if len(df_price) > 1 else curr_p
    curr_marcap = (curr_p * stocks_count) / UNIT
    updown      = ((curr_p / prev_p) - 1) * 100
    last_date   = df_price.index[-1].strftime("%m.%d")

    col_p     = COL_MAP.get(val_type, "영업이익")

    def _get_tp(year: int):
        row = fin_df[fin_df["Year"] == year]
        if row.empty or pd.isna(row[col_p].values[0]) or row[col_p].values[0] <= 0:
            return 0.0, 0.0, 0.0
        val = float(row[col_p].values[0])
        if "EBITDA" in val_type:
            tp      = curr_p * (target_mult / val)
            upside  = (tp / curr_p - 1) * 100
            tgt_mc  = curr_marcap * (target_mult / val)
        else:
            tgt_mc  = val * UNIT * target_mult
            tp      = tgt_mc / stocks_count if stocks_count > 0 else 0
            upside  = (tp / curr_p - 1) * 100
            tgt_mc  /= UNIT
        return float(tp), float(upside), float(tgt_mc)

    y1 = datetime.today().year
    y2 = y1 + 1
    tp1, up1, tm1 = _get_tp(y1)
    tp2, up2, tm2 = _get_tp(y2)

    card1 = _card(f"현재가 ({last_date})", f"{curr_p:,.0f}원",
                  f"{curr_marcap:,.0f}억", f"{updown:+.2f}%", updown > 0, is_zero=(updown == 0))
    card2 = (_card(f"목표가 ({str(y1)[-2:]}년)", f"{tp1:,.0f}원", f"{tm1:,.0f}억",
                   f"목표대비 {up1:+.1f}%", up1 > 0)
             if tp1 > 0 else _card(f"목표가 ({str(y1)[-2:]}년)", "N/A", "-", "데이터 없음", False, True))
    card3 = (_card(f"목표가 ({str(y2)[-2:]}년)", f"{tp2:,.0f}원", f"{tm2:,.0f}억",
                   f"목표대비 {up2:+.1f}%", up2 > 0)
             if tp2 > 0 else _card(f"목표가 ({str(y2)[-2:]}년)", "N/A", "-", "데이터 없음", False, True))

    table_rows = []
    for _, row in fin_df.iterrows():
        d = {"Label": row["Label"] if "Label" in row else f"{row['Year']}년"}
        for c in COLS_TO_EDIT:
            val = row[c]
            d[c] = round(float(val), 1) if pd.notna(val) and val != 0 else None
        table_rows.append(d)

    chart_div = _build_chart(fin_df, df_price, stocks_count, curr_p, curr_marcap,
                             val_type, col_p, target_mult, chart_period or "전체")

    ticker_info = {"ticker": ticker, "name": corp_name, "stocks": stocks_count}
    fin_ser     = json.dumps(fin_df.to_dict("records"), default=str)
    price_ser   = json.dumps({
        "dates": [d.strftime("%Y-%m-%d") for d in df_price.index],
        "close": df_price["Close"].tolist(),
    })

    results = html.Div([
        html.H6(f"📊 {corp_name} ({ticker})", className="mb-3"),
        dbc.Row([dbc.Col(card1), dbc.Col(card2), dbc.Col(card3)], className="mb-4"),
        html.H6("📝 연도별 재무 데이터 (직접 수정 가능 → 저장)", className="mb-1"),
        html.Small("셀 더블클릭 → 값 입력 → 저장", className="text-muted d-block mb-2"),
        dag.AgGrid(
            id="val-fin-grid",
            columnDefs=FIN_COL_DEFS,
            rowData=table_rows,
            dashGridOptions={"domLayout": "autoHeight", "rowHeight": 40, "headerHeight": 36},
            defaultColDef={"resizable": True, "suppressMenu": True},
            style={"width": "100%"},
        ),
        dbc.Row([
            dbc.Col(dbc.Button("💾 추정치 저장", id="val-save-btn", color="primary",
                               size="sm", n_clicks=0), width="auto"),
        ], className="mt-2 mb-3"),
        html.Div(id="val-save-status", className="mb-2"),
    ])

    return results, chart_div, SHOWN, no_update, ticker_info, fin_ser, price_ser


@callback(
    Output("val-chart", "children", allow_duplicate=True),
    Input("val-chart-period",  "value"),
    State("val-ticker-info",   "data"),
    State("val-fin-data",      "data"),
    State("val-price-data",    "data"),
    State("val-type-select",   "value"),
    State("val-mult-input",    "value"),
    prevent_initial_call=True,
)
def update_chart_period(chart_period, ticker_info, fin_ser, price_ser, val_type, target_mult):
    if not ticker_info or not fin_ser or not price_ser:
        return no_update

    fin_df = pd.DataFrame(json.loads(fin_ser))
    pd.to_datetime(fin_df["Year"].astype(str), format="%Y", errors="coerce")

    price_data = json.loads(price_ser)
    df_price   = pd.DataFrame(
        {"Close": price_data["close"]},
        index=pd.to_datetime(price_data["dates"]),
    )

    val_type    = val_type or "POR(영업익)"
    col_p       = COL_MAP.get(val_type, "영업이익")
    stocks      = ticker_info.get("stocks", 1)
    curr_p      = float(df_price.iloc[-1]["Close"])
    curr_marcap = (curr_p * stocks) / UNIT
    target      = float(target_mult or 12)

    return _build_chart(fin_df, df_price, stocks, curr_p, curr_marcap,
                        val_type, col_p, target, chart_period or "전체")


@callback(
    Output("val-save-status", "children"),
    Input("val-save-btn", "n_clicks"),
    State("val-fin-grid", "rowData"),
    State("val-ticker-info", "data"),
    State("val-fin-data",    "data"),
    prevent_initial_call=True,
)
def save_estimates(n_clicks, grid_rows, ticker_info, orig_fin_ser):
    if not grid_rows or not ticker_info:
        return no_update

    ticker   = ticker_info.get("ticker")
    orig_fin = pd.DataFrame(json.loads(orig_fin_ser)) if orig_fin_ser else None

    new_est = load_user_estimates()
    if ticker not in new_est:
        new_est[ticker] = {}

    for i, row in enumerate(grid_rows):
        yr = str(orig_fin.iloc[i]["Year"]) if orig_fin is not None else str(2021 + i)
        if yr not in new_est[ticker]:
            new_est[ticker][yr] = {}
        for col in COLS_TO_EDIT:
            orig_val   = float(orig_fin.iloc[i][col]) if orig_fin is not None and pd.notna(orig_fin.iloc[i][col]) else 0
            edited_val = float(row.get(col) or 0)
            if orig_val == 0 and edited_val != 0:
                new_est[ticker][yr][col] = edited_val
            elif orig_val != 0:
                new_est[ticker][yr].pop(col, None)

        if not new_est[ticker].get(yr):
            new_est[ticker].pop(yr, None)

    if not new_est.get(ticker):
        new_est.pop(ticker, None)

    ok, err = save_to_github(
        ESTIMATES_FILE,
        json.dumps(new_est, indent=4, ensure_ascii=False),
        f"Update {ticker_info.get('name', '')} estimates",
    )
    if ok:
        get_hybrid_financials.clear()
        load_user_estimates.clear()
        return dbc.Alert("✅ 저장됐습니다.", color="success", duration=3000, dismissable=True)
    return dbc.Alert(f"저장 실패: {err}", color="danger", duration=4000, dismissable=True)
