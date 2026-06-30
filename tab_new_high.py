"""
tab_new_high.py — 신고가 트래킹 tab.
"""
import json
import os
import pandas as pd
import dash_bootstrap_components as dbc
from dash import html, dcc, Input, Output, State, callback, ctx, no_update, dash_table

from data_layer import save_to_github, GITHUB_REPO, GITHUB_BRANCH

DATA_PATH = "data/new_high"


def _get_report_files() -> list[str]:
    if not os.path.exists(DATA_PATH):
        return []
    files = [
        f for f in os.listdir(DATA_PATH)
        if (f.startswith("report_") or f.startswith("newhigh_")) and f.endswith(".json")
    ]
    return sorted(files, reverse=True)


def _format_filename(f: str) -> str:
    try:
        date_part = f.replace("report_", "").replace("newhigh_", "").replace(".json", "")
        return (
            f"{date_part[:4]}년 {date_part[4:6]}월 {date_part[6:8]}일 "
            f"{date_part[9:11]}:{date_part[11:13]} 분석본"
        )
    except:
        return f


def layout():
    files = _get_report_files()

    if not files:
        return html.Div([
            html.H5("🚀 신고가 트래킹"),
            dbc.Alert("분석된 데이터가 없습니다. 장 마감 후 자동 배치가 실행될 때까지 기다려주세요.", color="warning"),
        ])

    options = [{"label": _format_filename(f), "value": f} for f in files]

    return html.Div([
        dbc.Row([
            dbc.Col(html.H5("🚀 신고가 트래킹", className="mb-0"), width="auto"),
            dbc.Col(
                dbc.Button("🔄 새로고침", id="nh-refresh-btn", color="light", size="sm", n_clicks=0),
                width="auto", className="ms-auto"
            ),
        ], className="mb-3 align-items-center"),

        dbc.Row([
            dbc.Col(
                dcc.Dropdown(
                    id="nh-file-dropdown",
                    options=options,
                    value=files[0] if files else None,
                    clearable=False,
                    style={"fontSize": "13px"},
                ),
                width=6,
            ),
            dbc.Col(
                dbc.Row([
                    dbc.Col(
                        dcc.Dropdown(
                            id="nh-period-filter",
                            options=[
                                {"label": "전체", "value": "전체"},
                                {"label": "1년(52주) 신고가", "value": "1년(52주) 신고가"},
                                {"label": "6개월 신고가",     "value": "6개월 신고가"},
                                {"label": "3개월 신고가",     "value": "3개월 신고가"},
                            ],
                            value="전체",
                            clearable=False,
                            style={"fontSize": "13px"},
                        ),
                        width=6,
                    ),
                    dbc.Col(
                        dcc.Dropdown(
                            id="nh-marcap-filter",
                            options=[
                                {"label": "전체",                      "value": "전체"},
                                {"label": "500억~5000억 (중소형)",     "value": "중소형"},
                                {"label": "5000억 이상 (대형)",        "value": "대형"},
                            ],
                            value="전체",
                            clearable=False,
                            style={"fontSize": "13px"},
                        ),
                        width=6,
                    ),
                ], className="g-2"),
                width=6,
            ),
        ], className="mb-3 g-2"),

        html.Div(id="nh-status", className="mb-2"),
        html.Div(id="nh-table-container"),
        html.Div(id="nh-news-container", className="mt-4"),
    ])


@callback(
    Output("nh-table-container", "children"),
    Output("nh-news-container", "children"),
    Input("nh-file-dropdown",   "value"),
    Input("nh-period-filter",   "value"),
    Input("nh-marcap-filter",   "value"),
    Input("nh-refresh-btn",     "n_clicks"),
    prevent_initial_call=False,
)
def render_table(selected_file, period_filter, marcap_filter, _refresh):
    if not selected_file:
        return dbc.Alert("파일을 선택해주세요.", color="secondary"), html.Div()

    try:
        with open(f"{DATA_PATH}/{selected_file}", "r", encoding="utf-8") as fh:
            report_data = json.load(fh)
    except Exception as e:
        return dbc.Alert(f"파일 로드 실패: {e}", color="danger"), html.Div()

    results = report_data.get("results", [])
    if not results:
        return dbc.Alert("조건을 만족하는 주도주가 없습니다.", color="info"), html.Div()

    df = pd.DataFrame(results)
    if "시가총액" not in df.columns:
        df["시가총액"] = 0

    # Filters
    if period_filter != "전체":
        df = df[df["돌파기간"] == period_filter]
    if marcap_filter == "중소형":
        df = df[(df["시가총액"] >= 50_000_000_000) & (df["시가총액"] < 500_000_000_000)]
    elif marcap_filter == "대형":
        df = df[df["시가총액"] >= 500_000_000_000]

    if df.empty:
        return dbc.Alert("해당 필터 조건에 맞는 종목이 없습니다.", color="warning"), html.Div()

    # Format
    disp = df.copy()
    disp["등락률"]   = disp["등락률"].apply(lambda x: f"{float(x):.2f}%" if not isinstance(x, str) else x)
    disp["시가총액"] = disp["시가총액"].apply(lambda x: f"{int(x)//100_000_000:,}억" if pd.notnull(x) and x > 0 else "N/A")
    disp["네이버"]   = "https://finance.naver.com/item/main.naver?code=" + disp["코드"]

    cols_show = ["종목명", "등락률", "시가총액", "추정 사유", "돌파기간"]
    if "최신뉴스" in disp.columns:
        cols_show.append("최신뉴스")
    cols_show.append("네이버")

    table = dash_table.DataTable(
        id="nh-data-table",
        data=disp[cols_show].to_dict("records"),
        columns=[
            {"name": c, "id": c,
             "editable": c == "추정 사유",
             "presentation": "markdown" if c == "네이버" else "input"}
            for c in cols_show
        ],
        markdown_options={"html": True},
        style_table={"overflowX": "auto"},
        style_cell={
            "textAlign": "left", "padding": "6px 10px",
            "fontSize": "13px", "whiteSpace": "normal",
        },
        style_header={"fontWeight": "700", "backgroundColor": "#f8f9fa"},
        style_data_conditional=[
            {"if": {"column_id": "추정 사유"}, "backgroundColor": "#fffde7"},
            {"if": {"column_id": "등락률", "filter_query": '{등락률} contains "-"'},
             "color": "#1565C0"},
            {"if": {"column_id": "등락률", "filter_query": '{등락률} not contains "-"'},
             "color": "#C62828"},
        ],
        editable=True,
        row_selectable=False,
        page_size=30,
        sort_action="native",
    )

    header = html.H6(f"📝 통합 분석 결과 ({len(disp)}건)", className="mb-2")
    save_btn = dbc.Button("💾 사유 저장", id="nh-save-btn", color="primary",
                          size="sm", n_clicks=0, className="mb-2")
    note = html.Small("'추정 사유' 셀 클릭 후 수정 → [💾 사유 저장] 클릭",
                      className="text-muted ms-2")

    # News accordion
    news_items = []
    for item in results:
        name     = item.get("종목명", "알 수 없음")
        news_md  = item.get("뉴스목록", "관련 뉴스 없음")
        news_items.append(
            dbc.AccordionItem(
                dcc.Markdown(news_md),
                title=f"[{name}] 주요 뉴스",
            )
        )

    news_section = html.Div([
        html.Hr(),
        html.H6("🔍 주요 뉴스 모니터링"),
        dbc.Accordion(news_items, start_collapsed=True, flush=True),
    ]) if news_items else html.Div()

    return html.Div([header, dbc.Row([dbc.Col(save_btn), dbc.Col(note)]), table]), news_section


@callback(
    Output("nh-status", "children"),
    Input("nh-save-btn", "n_clicks"),
    State("nh-data-table", "data"),
    State("nh-file-dropdown", "value"),
    prevent_initial_call=True,
)
def save_comments(n_clicks, table_data, selected_file):
    if not table_data or not selected_file:
        return no_update

    try:
        with open(f"{DATA_PATH}/{selected_file}", "r", encoding="utf-8") as fh:
            report_data = json.load(fh)
    except:
        return dbc.Alert("파일 로드 실패", color="danger", duration=3000)

    comment_map = {row["종목명"]: row.get("추정 사유", "") for row in table_data}
    for item in report_data.get("results", []):
        name = item.get("종목명", "")
        if name in comment_map:
            item["추정 사유"] = comment_map[name]

    content = json.dumps(report_data, indent=4, ensure_ascii=False)
    ok, err  = save_to_github(f"{DATA_PATH}/{selected_file}", content, "Update comments via Web UI")
    if ok:
        return dbc.Alert("✅ 저장됐습니다!", color="success", duration=3000, dismissable=True)
    return dbc.Alert(f"저장 실패: {err}", color="danger", duration=4000, dismissable=True)
