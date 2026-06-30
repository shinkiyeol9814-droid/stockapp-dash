import dash
from dash import dcc, html, Input, Output, callback
import dash_bootstrap_components as dbc

# Import tab modules — callbacks are registered on import
import tab_watchlist
import tab_new_high
import tab_report
import tab_earnings
import tab_telegram
import tab_valuation

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.BOOTSTRAP],
    suppress_callback_exceptions=True,
    title="StkPro 통합 보드",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server  # expose Flask server for gunicorn

NAV_TABS = [
    ("valuation",  "📈 가치평가"),
    ("new-high",   "🚀 신고가"),
    ("report",     "📰 레포트"),
    ("earnings",   "📊 실적"),
    ("telegram",   "💬 텔레그램"),
    ("watchlist",  "📋 워치리스트"),
]

app.layout = dbc.Container(
    [
        dcc.Interval(id="global-interval", interval=3 * 60 * 1000, n_intervals=0),
        dcc.Store(id="active-tab", storage_type="session", data="watchlist"),

        # ── Navigation ──────────────────────────────────────────────────────────
        html.Div(
            dbc.ButtonGroup(
                [
                    dbc.Button(label, id=f"nav-{tab_id}", color="light", size="sm", n_clicks=0)
                    for tab_id, label in NAV_TABS
                ],
                style={"flexWrap": "wrap", "gap": "2px"},
            ),
            className="py-2 px-1 mb-3",
            style={
                "position": "sticky",
                "top": 0,
                "zIndex": 1000,
                "backgroundColor": "#fff",
                "borderBottom": "1px solid #dee2e6",
                "boxShadow": "0 2px 4px rgba(0,0,0,.06)",
            },
        ),

        # ── Tab content ─────────────────────────────────────────────────────────
        html.Div(id="page-content"),
    ],
    fluid=True,
    style={"paddingLeft": "12px", "paddingRight": "12px"},
)


@callback(
    Output("page-content", "children"),
    Output("active-tab", "data"),
    *[Input(f"nav-{tab_id}", "n_clicks") for tab_id, _ in NAV_TABS],
    Input("active-tab", "data"),
    prevent_initial_call=False,
)
def render_tab(*args):
    from dash import ctx

    current_tab = args[-1]  # last arg is active-tab store value

    tab_map = {
        "valuation": tab_valuation.layout,
        "new-high":  tab_new_high.layout,
        "report":    tab_report.layout,
        "earnings":  tab_earnings.layout,
        "telegram":  tab_telegram.layout,
        "watchlist": tab_watchlist.layout,
    }

    triggered = ctx.triggered_id or f"nav-{current_tab}"

    for tab_id, _ in NAV_TABS:
        if triggered == f"nav-{tab_id}":
            return tab_map[tab_id](), tab_id

    return tab_map.get(current_tab, tab_watchlist.layout)(), current_tab


if __name__ == "__main__":
    app.run(debug=True)
