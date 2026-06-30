"""
tab_watchlist.py — Watchlist tab with AG Grid drag-to-reorder.
"""
import json
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import dcc, html, Input, Output, State, callback, ctx, no_update

from data_layer import (
    METHODS, COL_MAP, CUR_YEAR, NEXT_YEAR,
    load_watchlist, save_watchlist,
    get_ticker_listing, get_live_price, get_watch_financials,
    calc_target, calc_current_mult,
    get_live_price,
)

# ── Column definitions ─────────────────────────────────────────────────────────
_UPSIDE_STYLE = {
    "function": (
        "params.value == null ? {} : params.value < 0 "
        "? {'color': '#ef5350', 'fontWeight': '700'} "
        ": {'color': '#26a69a', 'fontWeight': '700'}"
    )
}
_UPSIDE_FMT = {
    "function": (
        "params.value == null ? 'N/A' "
        ": (params.value > 0 ? '▲ +' : '▼ ') + params.value.toFixed(1) + '%'"
    )
}
_PRICE_FMT = {
    "function": "params.value ? params.value.toLocaleString('ko-KR') + '원' : '-'"
}
_MULT_FMT  = {"function": "params.value != null ? params.value.toFixed(1) + 'x' : 'N/A'"}

COLUMN_DEFS = [
    {
        "field": "name", "headerName": "종목명",
        "rowDrag": True, "width": 150,
        "cellStyle": {"fontWeight": "600"},
        "pinned": "left",
    },
    {
        "field": "price", "headerName": "현재가", "width": 120,
        "valueFormatter": _PRICE_FMT,
        "type": "numericColumn",
    },
    {
        "field": "change", "headerName": "등락률", "width": 90,
        "valueFormatter": {
            "function": (
                "params.value != null "
                "? (params.value > 0 ? '+' : '') + params.value.toFixed(2) + '%' "
                ": '-'"
            )
        },
        "cellStyle": {
            "function": (
                "params.value == null ? {} : params.value < 0 "
                "? {'color': '#1565C0'} : {'color': '#C62828'}"
            )
        },
    },
    {
        "field": "method", "headerName": "평가방식", "width": 145,
        "editable": True,
        "cellEditor": "agSelectCellEditor",
        "cellEditorParams": {"values": METHODS},
        "cellStyle": {"cursor": "pointer"},
    },
    {
        "field": "multiple", "headerName": "목표배수", "width": 105,
        "editable": True,
        "cellEditor": "agNumberCellEditor",
        "cellEditorParams": {"precision": 1, "step": 0.5},
        "valueFormatter": _MULT_FMT,
        "type": "numericColumn",
        "cellStyle": {"cursor": "pointer"},
    },
    {
        "field": "curr_m", "headerName": "현재배수", "width": 100,
        "valueFormatter": _MULT_FMT,
        "type": "numericColumn",
        "cellStyle": {"color": "#555"},
    },
    {
        "field": "tp_26", "headerName": f"{CUR_YEAR}E 목표가", "width": 125,
        "valueFormatter": _PRICE_FMT,
        "type": "numericColumn",
        "cellStyle": {"color": "#666", "fontSize": "12px"},
    },
    {
        "field": "up_26", "headerName": f"{CUR_YEAR}E 업사이드", "width": 125,
        "valueFormatter": _UPSIDE_FMT,
        "cellStyle": _UPSIDE_STYLE,
        "type": "numericColumn",
    },
    {
        "field": "tp_27", "headerName": "27E 목표가", "width": 125,
        "valueFormatter": _PRICE_FMT,
        "type": "numericColumn",
        "cellStyle": {"color": "#666", "fontSize": "12px"},
    },
    {
        "field": "up_27", "headerName": "27E 업사이드", "width": 125,
        "valueFormatter": _UPSIDE_FMT,
        "cellStyle": _UPSIDE_STYLE,
        "type": "numericColumn",
    },
    {
        "field": "code", "headerName": "", "width": 0,
        "hide": True,
    },
    {
        "headerName": "선택",
        "checkboxSelection": True,
        "headerCheckboxSelection": True,
        "width": 70,
        "pinned": "right",
        "suppressMenu": True,
    },
]

GRID_OPTIONS = {
    "rowDragManaged": True,
    "animateRows": True,
    "rowSelection": "multiple",
    "suppressRowClickSelection": True,
    "domLayout": "autoHeight",
    "rowHeight": 48,
    "headerHeight": 36,
}

# ── Helpers ────────────────────────────────────────────────────────────────────
def _build_row(code: str, cfg: dict, fin_df, stocks: int, price, change, name: str) -> dict:
    method   = cfg.get("method",   "POR(영업익)")
    multiple = float(cfg.get("multiple", 12.0))
    tp_26, up_26 = calc_target(fin_df, stocks, method, multiple, price, CUR_YEAR)
    tp_27, up_27 = calc_target(fin_df, stocks, method, multiple, price, NEXT_YEAR)
    curr_m       = calc_current_mult(fin_df, stocks, method, price, CUR_YEAR)
    return {
        "code":     code,
        "name":     name,
        "price":    price,
        "change":   change,
        "method":   method,
        "multiple": multiple,
        "curr_m":   curr_m,
        "tp_26":    round(tp_26)  if tp_26  else None,
        "up_26":    round(up_26, 1)  if up_26  is not None else None,
        "tp_27":    round(tp_27)  if tp_27  else None,
        "up_27":    round(up_27, 1)  if up_27  is not None else None,
    }

def _load_all_rows(watchlist: dict, local_settings: dict | None = None) -> list:
    rows = []
    for code, cfg in watchlist.items():
        merged = dict(cfg)
        if local_settings and code in local_settings:
            merged.update(local_settings[code])
        fin_df, stocks     = get_watch_financials(code)
        price, change, name = get_live_price(code)
        rows.append(_build_row(code, merged, fin_df, stocks, price, change, name))
    return rows

# ── Layout ─────────────────────────────────────────────────────────────────────
def layout():
    return html.Div([
        dcc.Store(id="wl-local-settings", storage_type="session", data={}),
        dcc.Interval(id="wl-interval", interval=3 * 60 * 1000, n_intervals=0),

        # Header
        dbc.Row([
            dbc.Col(html.H5("📋 밸류 워치리스트", className="mb-0"), width="auto"),
            dbc.Col(
                html.Small("셀 더블클릭 편집 · 행 드래그 정렬 · 현재가 60초 갱신",
                           className="text-muted"),
                className="d-flex align-items-center"
            ),
        ], className="mb-2 align-items-center"),

        # Add stock row
        dbc.Row([
            dbc.Col(
                dbc.Input(id="wl-search-input", placeholder="종목명 검색…", size="sm",
                          debounce=True),
                width=3
            ),
            dbc.Col(
                dcc.Dropdown(id="wl-code-dropdown", placeholder="종목 선택",
                             style={"fontSize": "13px"}),
                width=4
            ),
            dbc.Col(
                dbc.Button("➕ 추가", id="wl-add-btn", color="primary",
                           size="sm", n_clicks=0),
                width="auto"
            ),
            dbc.Col(
                dbc.Button("💾 순서 저장", id="wl-save-order-btn", color="secondary",
                           size="sm", n_clicks=0, outline=True),
                width="auto"
            ),
            dbc.Col(
                dbc.Button("🔄 새로고침", id="wl-refresh-btn", color="light",
                           size="sm", n_clicks=0),
                width="auto"
            ),
            dbc.Col(
                dbc.Button("🗑️ 선택 삭제", id="wl-delete-btn", color="danger",
                           size="sm", outline=True, n_clicks=0),
                width="auto"
            ),
        ], className="mb-2 g-2 align-items-center"),

        # Status alert
        html.Div(id="wl-status", className="mb-2"),

        # AG Grid
        dag.AgGrid(
            id="wl-grid",
            columnDefs=COLUMN_DEFS,
            rowData=[],
            dashGridOptions=GRID_OPTIONS,
            defaultColDef={"resizable": True, "suppressMenu": True},
            columnSizeOptions={"defaultMinWidth": 70},
            style={"width": "100%"},
        ),
    ])


# ── Callbacks ──────────────────────────────────────────────────────────────────

# 1) Search → populate dropdown
@callback(
    Output("wl-code-dropdown", "options"),
    Input("wl-search-input", "value"),
    prevent_initial_call=True,
)
def search_stocks(q: str):
    if not q or len(q) < 1:
        return []
    listing  = get_ticker_listing()
    filtered = listing[listing["Name"].str.contains(q, case=False, na=False)].head(10)
    return [{"label": f"{r['Name']} ({r['Code']})", "value": r["Code"]}
            for _, r in filtered.iterrows()]


# 2) Grid data: load on interval / refresh / add / delete (master reload)
@callback(
    Output("wl-grid", "rowData"),
    Output("wl-status", "children"),
    Output("wl-local-settings", "data"),
    Input("wl-interval", "n_intervals"),
    Input("wl-add-btn", "n_clicks"),
    Input("wl-delete-btn", "n_clicks"),
    Input("wl-refresh-btn", "n_clicks"),
    State("wl-code-dropdown", "value"),
    State("wl-grid", "selectedRows"),
    State("wl-local-settings", "data"),
    prevent_initial_call=False,
)
def update_grid(n_int, n_add, n_delete, n_refresh,
                selected_code, selected_rows, local_settings):
    triggered = ctx.triggered_id
    local_settings = local_settings or {}

    # ── Refresh: clear caches ──────────────────────────────────────────────────
    if triggered == "wl-refresh-btn":
        get_live_price.clear()
        get_watch_financials.clear()
        load_watchlist.clear()
        local_settings = {}

    # ── Add stock ──────────────────────────────────────────────────────────────
    if triggered == "wl-add-btn" and selected_code:
        wl = load_watchlist()
        if selected_code not in wl:
            new_wl = dict(wl)
            new_wl[selected_code] = {"method": "POR(영업익)", "multiple": 12.0}
            if save_watchlist(new_wl):
                status = dbc.Alert("✅ 추가됐습니다.", color="success", duration=3000, dismissable=True)
            else:
                status = dbc.Alert("❌ 저장 실패 (GH_PAT 확인)", color="danger", duration=4000, dismissable=True)
        else:
            status = dbc.Alert("이미 추가된 종목입니다.", color="warning", duration=3000, dismissable=True)
            wl_data = load_watchlist()
            rows    = _load_all_rows(wl_data, local_settings)
            return rows, status, local_settings
        wl_data = load_watchlist()
        rows    = _load_all_rows(wl_data, local_settings)
        return rows, status, local_settings

    # ── Delete selected ────────────────────────────────────────────────────────
    if triggered == "wl-delete-btn" and selected_rows:
        wl      = load_watchlist()
        codes   = {r["code"] for r in selected_rows if "code" in r}
        new_wl  = {c: v for c, v in wl.items() if c not in codes}
        for c in codes:
            local_settings.pop(c, None)
        if save_watchlist(new_wl):
            status = dbc.Alert(f"🗑️ {len(codes)}개 삭제됐습니다.", color="info", duration=3000, dismissable=True)
        else:
            status = dbc.Alert("삭제 저장 실패", color="danger", duration=3000, dismissable=True)
        wl_data = load_watchlist()
        rows    = _load_all_rows(wl_data, local_settings)
        return rows, status, local_settings

    # ── Default: load / interval refresh ──────────────────────────────────────
    wl_data = load_watchlist()
    if not wl_data:
        return [], dbc.Alert("종목을 추가해주세요.", color="secondary"), local_settings

    rows = _load_all_rows(wl_data, local_settings)
    return rows, no_update, local_settings


# 3) Cell edited → recalculate only that row, save settings to local store
@callback(
    Output("wl-grid", "rowTransaction"),
    Output("wl-local-settings", "data", allow_duplicate=True),
    Input("wl-grid", "cellValueChanged"),
    State("wl-local-settings", "data"),
    prevent_initial_call=True,
)
def on_cell_edit(changed_cells, local_settings):
    if not changed_cells:
        return no_update, no_update

    local_settings = local_settings or {}
    updates = []

    for cell in changed_cells:
        row_data = cell.get("data", {})
        code     = row_data.get("code")
        if not code:
            continue
        method   = row_data.get("method",   "POR(영업익)")
        multiple = float(row_data.get("multiple", 12.0))

        # Persist to local settings
        local_settings[code] = {"method": method, "multiple": multiple}

        # Recalculate
        fin_df, stocks      = get_watch_financials(code)
        price               = row_data.get("price")
        tp_26, up_26        = calc_target(fin_df, stocks, method, multiple, price, CUR_YEAR)
        tp_27, up_27        = calc_target(fin_df, stocks, method, multiple, price, NEXT_YEAR)
        curr_m              = calc_current_mult(fin_df, stocks, method, price, CUR_YEAR)

        updates.append({
            **row_data,
            "method":   method,
            "multiple": multiple,
            "curr_m":   curr_m,
            "tp_26":    round(tp_26)    if tp_26  else None,
            "up_26":    round(up_26, 1) if up_26  is not None else None,
            "tp_27":    round(tp_27)    if tp_27  else None,
            "up_27":    round(up_27, 1) if up_27  is not None else None,
        })

    return {"update": updates}, local_settings


# 4) Save current row order to GitHub
@callback(
    Output("wl-status", "children", allow_duplicate=True),
    Input("wl-save-order-btn", "n_clicks"),
    State("wl-grid", "virtualRowData"),
    State("wl-local-settings", "data"),
    prevent_initial_call=True,
)
def save_order(n_clicks, virtual_rows, local_settings):
    if not virtual_rows:
        return no_update

    local_settings = local_settings or {}
    wl_gh = load_watchlist()

    new_wl = {}
    for row in virtual_rows:
        code = row.get("code")
        if not code:
            continue
        base = wl_gh.get(code, {})
        loc  = local_settings.get(code, {})
        new_wl[code] = {
            "method":   loc.get("method",   base.get("method",   "POR(영업익)")),
            "multiple": float(loc.get("multiple", base.get("multiple", 12.0))),
        }

    if save_watchlist(new_wl):
        return dbc.Alert("💾 순서·설정 저장됐습니다.", color="success", duration=3000, dismissable=True)
    return dbc.Alert("저장 실패 (GH_PAT 확인)", color="danger", duration=4000, dismissable=True)
