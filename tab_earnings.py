"""
tab_earnings.py — 실적 스크리닝 (AWAKE) tab.
"""
import json, os, re
from pathlib import Path
import dash_bootstrap_components as dbc
from dash import html, dcc, Input, Output, State, callback, ctx, no_update, ALL

from data_layer import save_to_github, GITHUB_REPO

BASE_DIR          = Path(__file__).parent
DATA_FILE         = BASE_DIR / "data" / "earnings" / "earnings_data.json"
FAVORITES_FILE    = "data/earnings/favorites.json"
LOCAL_FAV_FILE    = BASE_DIR / "data" / "earnings" / "favorites.json"
BATCH_CONFIG_FILE = "data/earnings/batch_config.json"
LOCAL_BATCH_FILE  = BASE_DIR / "data" / "earnings" / "batch_config.json"


# ── GitHub helpers ──────────────────────────────────────────────────────────────
from data_layer import _gh_headers, GITHUB_BRANCH
import requests, base64

def _load_json_from_github(path: str, default):
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}?ref={GITHUB_BRANCH}"
        res = requests.get(url, headers=_gh_headers(), timeout=7)
        if res.status_code == 200:
            return json.loads(base64.b64decode(res.json()["content"]).decode())
    except:
        pass
    return default

def _save_json_to_github(path: str, data, message: str) -> bool:
    content = json.dumps(data, ensure_ascii=False)
    ok, _   = save_to_github(path, content, message)
    return ok


def _load_favorites() -> set:
    data = _load_json_from_github(FAVORITES_FILE, None)
    if data is not None:
        return set(data)
    if LOCAL_FAV_FILE.exists():
        with open(LOCAL_FAV_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def _save_favorites(favs: set) -> bool:
    return _save_json_to_github(FAVORITES_FILE, list(favs), "Update favorites")

def _load_batch_config() -> bool:
    data = _load_json_from_github(BATCH_CONFIG_FILE, None)
    if data is not None:
        return data.get("enabled", True)
    if LOCAL_BATCH_FILE.exists():
        with open(LOCAL_BATCH_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("enabled", True)
    return True

def _save_batch_config(enabled: bool) -> bool:
    return _save_json_to_github(BATCH_CONFIG_FILE, {"enabled": enabled},
                                f"Batch {'ON' if enabled else 'OFF'}")

def _load_earnings():
    if not DATA_FILE.exists():
        return None
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _get_growth_color(val: str) -> str:
    if not val:
        return "#555555"
    if "+" in val or "흑전" in val:
        return "#C62828"
    if "-" in val or "적전" in val or "적자" in val:
        return "#1565C0"
    return "#555555"


# ── Layout ─────────────────────────────────────────────────────────────────────
def layout():
    return html.Div([
        dcc.Store(id="ea-favorites", storage_type="session"),
        dcc.Store(id="ea-batch-enabled", storage_type="session"),
        dcc.Store(id="ea-show-favs", data=False, storage_type="session"),

        # Header row
        dbc.Row([
            dbc.Col(html.H5("📈 실적 스크리닝 (AWAKE)", className="mb-0"), width="auto"),
            dbc.Col(
                dbc.ButtonGroup([
                    dbc.Button("", id="ea-batch-btn",   size="sm", n_clicks=0),
                    dbc.Button("⭐ 관심종목", id="ea-fav-btn", color="light", size="sm",
                               outline=True, n_clicks=0),
                    dbc.Button("🔄 새로고침", id="ea-refresh-btn", color="light", size="sm",
                               n_clicks=0),
                ]),
                width="auto", className="ms-auto",
            ),
        ], className="mb-3 align-items-center"),

        html.Div(id="ea-status", className="mb-2"),
        html.Div(id="ea-content"),
    ])


# ── Callbacks ───────────────────────────────────────────────────────────────────

@callback(
    Output("ea-favorites",    "data"),
    Output("ea-batch-enabled","data"),
    Output("ea-batch-btn",    "children"),
    Output("ea-batch-btn",    "color"),
    Input("ea-refresh-btn",   "n_clicks"),
    prevent_initial_call=False,
)
def init_state(_refresh):
    favs    = list(_load_favorites())
    enabled = _load_batch_config()
    label   = "🟢 수집 ON" if enabled else "🔴 수집 OFF"
    color   = "success"   if enabled else "danger"
    return favs, enabled, label, color


@callback(
    Output("ea-batch-enabled", "data", allow_duplicate=True),
    Output("ea-batch-btn",     "children", allow_duplicate=True),
    Output("ea-batch-btn",     "color",    allow_duplicate=True),
    Input("ea-batch-btn",      "n_clicks"),
    State("ea-batch-enabled",  "data"),
    prevent_initial_call=True,
)
def toggle_batch(n_clicks, enabled):
    new_state = not bool(enabled)
    _save_batch_config(new_state)
    label = "🟢 수집 ON" if new_state else "🔴 수집 OFF"
    color = "success"   if new_state else "danger"
    return new_state, label, color


@callback(
    Output("ea-show-favs", "data"),
    Output("ea-fav-btn",   "outline"),
    Input("ea-fav-btn",    "n_clicks"),
    State("ea-show-favs",  "data"),
    prevent_initial_call=True,
)
def toggle_fav_filter(n, show):
    new_show = not bool(show)
    return new_show, not new_show


@callback(
    Output("ea-content",    "children"),
    Output("ea-status",     "children"),
    Output("ea-favorites",  "data", allow_duplicate=True),
    Input("ea-favorites",   "data"),
    Input("ea-show-favs",   "data"),
    prevent_initial_call=False,
)
def render_earnings(favorites_list, show_favs):
    favorites = set(favorites_list or [])
    results   = _load_earnings()

    if results is None:
        return dbc.Alert("📂 수집된 실적 데이터가 없습니다.", color="info"), html.Div(), no_update
    if not results:
        return dbc.Alert("분석된 실적 데이터가 없습니다.", color="warning"), html.Div(), no_update

    if show_favs:
        filtered = [r for r in results if r.get("종목코드", r.get("name", "")) in favorites
                    or r.get("종목명", "") in favorites]
        if not filtered:
            return (
                html.Div(),
                dbc.Alert("관심 종목이 없습니다.", color="info"),
                no_update,
            )
        display_results = filtered
    else:
        display_results = results

    cards = []
    for item in display_results:
        name    = item.get("종목명", item.get("name", "알 수 없음"))
        code    = item.get("종목코드", item.get("code", ""))
        is_fav  = name in favorites or code in favorites

        # Build earnings rows
        rows = []
        for period, data in item.items():
            if period in ["종목명", "종목코드", "name", "code"]:
                continue
            if not isinstance(data, dict):
                continue
            cells = []
            for col_name, val in data.items():
                color = _get_growth_color(str(val)) if isinstance(val, str) else "#333"
                cells.append(
                    html.Td(str(val) if val is not None else "-",
                            style={"color": color, "fontSize": "12px",
                                   "padding": "4px 8px", "whiteSpace": "nowrap"})
                )
            rows.append(html.Tr([html.Td(period, style={"fontWeight": "600",
                                                         "fontSize": "12px",
                                                         "padding": "4px 8px",
                                                         "whiteSpace": "nowrap",
                                                         "backgroundColor": "#f8f9fa"})] + cells))

        fav_icon = "⭐" if is_fav else "☆"
        card = dbc.Card([
            dbc.CardHeader(
                dbc.Row([
                    dbc.Col(html.B(name, style={"fontSize": "14px"}), width="auto"),
                    dbc.Col(
                        dbc.Button(fav_icon, id={"type": "ea-fav-toggle", "index": name},
                                   size="sm", color="warning" if is_fav else "light",
                                   n_clicks=0, className="py-0 px-2"),
                        width="auto", className="ms-auto",
                    ),
                ], align="center"),
            ),
            dbc.CardBody(
                html.Table([html.Tbody(rows)],
                           style={"width": "100%", "borderCollapse": "collapse"}),
                style={"padding": "8px", "overflowX": "auto"},
            ),
        ], className="mb-2")

        cards.append(card)

    return html.Div(cards), no_update, no_update


@callback(
    Output("ea-favorites",  "data", allow_duplicate=True),
    Output("ea-status",     "children", allow_duplicate=True),
    Input({"type": "ea-fav-toggle", "index": ALL}, "n_clicks"),
    State("ea-favorites", "data"),
    prevent_initial_call=True,
)
def toggle_favorite(n_clicks_list, favorites_list):
    from dash import ctx as _ctx
    triggered = _ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return no_update, no_update

    name      = triggered["index"]
    favorites = set(favorites_list or [])

    if name in favorites:
        favorites.discard(name)
        msg = f"⭐ {name} 관심 해제"
    else:
        favorites.add(name)
        msg = f"⭐ {name} 관심 추가"

    _save_favorites(favorites)
    status = dbc.Alert(msg, color="info", duration=2000, dismissable=True)
    return list(favorites), status
