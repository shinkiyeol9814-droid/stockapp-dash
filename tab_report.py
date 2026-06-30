"""
tab_report.py — 증권사 레포트 AI 요약 tab.
"""
import glob, json, os, re
import dash_bootstrap_components as dbc
from dash import html, dcc, Input, Output, callback, no_update


def _get_report_options() -> dict:
    options = {}
    for file_path in glob.glob("data/broker_report/*.json"):
        base = os.path.basename(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict) and "analysis_time" in data:
                r_type       = data.get("report_type", "")
                a_time       = data.get("analysis_time", "")
                kor_type     = ("☀️ 정규" if "Regular" in r_type
                                else "🌙 전일" if "Previous" in r_type else "기타")
                display_name = f"{kor_type} ({a_time} 업데이트)"
                sort_key     = a_time
            else:
                m = re.search(r"(\d{8}_\d{4})", base)
                if not m:
                    continue
                t            = m.group(1)
                formatted    = f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[9:11]}:{t[11:13]}"
                kor_type     = "☀️ 정규" if "regular" in base.lower() else "🌙 전일"
                display_name = f"{kor_type} ({formatted} 과거데이터)"
                sort_key     = formatted

            options[display_name] = {"path": file_path, "sort": sort_key}
        except:
            continue

    sorted_opt = dict(sorted(options.items(), key=lambda x: x[1]["sort"], reverse=True))
    return {k: v["path"] for k, v in sorted_opt.items()}


def layout():
    opts = _get_report_options()
    if not opts:
        return html.Div([
            html.H5("📊 증권사 레포트 AI 요약"),
            dbc.Alert("실행된 배치 파일이 없습니다. batch_report.py를 먼저 실행하세요.", color="info"),
        ])

    opt_list = [{"label": k, "value": v} for k, v in opts.items()]

    return html.Div([
        dbc.Row([
            dbc.Col(html.H5("📊 증권사 레포트 AI 요약", className="mb-0"), width="auto"),
            dbc.Col(
                dbc.Button("🔄 새로고침", id="rep-refresh-btn", color="light",
                           size="sm", n_clicks=0),
                width="auto", className="ms-auto",
            ),
        ], className="mb-3 align-items-center"),

        dbc.Row([
            dbc.Col(
                dcc.Dropdown(
                    id="rep-file-dropdown",
                    options=opt_list,
                    value=opt_list[0]["value"] if opt_list else None,
                    clearable=False,
                    style={"fontSize": "13px"},
                ),
                width=7,
            ),
        ], className="mb-3"),

        html.Div(id="rep-content"),
    ])


def _fire_badge(upside_val):
    if upside_val is None or (isinstance(upside_val, float) and upside_val != upside_val):
        return "❄️", "#808080"
    if upside_val >= 50: return "🔥🔥🔥", "#FF0000"
    if upside_val >= 30: return "🔥🔥",   "#FF4500"
    if upside_val >  0:  return "🔥",     "#FF8C00"
    return "💧", "#1E90FF"


@callback(
    Output("rep-content", "children"),
    Input("rep-file-dropdown", "value"),
    Input("rep-refresh-btn",   "n_clicks"),
    prevent_initial_call=False,
)
def render_report(file_path, _):
    if not file_path:
        return no_update

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return dbc.Alert(f"파일 로드 실패: {e}", color="danger")

    if isinstance(data, dict) and "results" in data:
        results       = data.get("results", [])
        analysis_time = data.get("analysis_time", "알 수 없음")
    else:
        results       = data if isinstance(data, list) else []
        analysis_time = "과거 데이터 (파일명 참조)"

    if not results:
        return dbc.Alert("해당 시간대에 분석된 종목 데이터가 없습니다.", color="warning")

    import pandas as pd
    df = pd.DataFrame(results)
    df["Upside_num"] = pd.to_numeric(df.get("Upside", 0), errors="coerce")
    df = df.sort_values("Upside_num", ascending=False)

    items = []

    # Top pick highlight
    header_items = [
        html.Small(
            f"📅 레포트 추출 및 분석 시점: {analysis_time} | 총 {len(results)}개 분석",
            className="text-muted d-block mb-2"
        )
    ]
    if not df["Upside_num"].isna().all():
        top = df.loc[df["Upside_num"].idxmax()]
        if top["Upside_num"] > 0:
            header_items.insert(0, dbc.Alert(
                f"🚀 오늘의 최고 기대 종목: {top.get('종목명','N/A')} "
                f"(기대수익률: {top['Upside_num']:.1f}% | {top.get('증권사','N/A')})",
                color="success", className="mb-2"
            ))

    for _, row in df.iterrows():
        upside_val = row.get("Upside_num")
        fire, up_color = _fire_badge(upside_val)
        upside_str = "N/A" if pd.isna(upside_val) else f"{upside_val:.1f}%"

        points = row.get("투자포인트", [])
        if isinstance(points, list):
            points_items = [html.Li(p, style={"marginBottom": "4px"}) for p in points]
        else:
            points_items = [html.Li(str(points))]

        title = html.Div([
            html.Span(row.get("종목명", "N/A"),
                      style={"fontWeight": "bold", "fontSize": "14px"}),
            html.Span(f" ({row.get('증권사','N/A')})",
                      className="text-muted", style={"fontSize": "12px"}),
            html.Span(f"  |  {row.get('레포트 제목','')}",
                      style={"fontSize": "12px", "color": "#555"}),
            html.Span(
                f"  |  🚀 {upside_str} {fire}",
                style={"color": up_color, "fontWeight": "bold", "fontSize": "13px", "float": "right"}
            ),
        ])

        body = html.Div([
            html.Div([
                html.Span(f"📊 {row.get('현재가','N/A')} ({row.get('현재시총','N/A')}) ➡️ "),
                html.B(f"{row.get('목표주가','N/A')} ({row.get('목표시총','N/A')})"),
                html.Span(f"  |  발행일: {row.get('발행일자','N/A')}",
                          className="text-muted ms-2"),
            ], className="mb-2", style={"fontSize": "13px"}),
            html.B("💡 핵심 투자 포인트", style={"color": "#0056b3"}),
            html.Ul(points_items, style={"marginTop": "4px", "paddingLeft": "20px",
                                          "fontSize": "13px"}),
            html.Div(
                f"평가 방식: {row.get('평가방식','N/A')}",
                className="text-muted mt-1",
                style={"fontSize": "12px", "backgroundColor": "#f9f9f9",
                       "padding": "5px 10px", "borderRadius": "4px"},
            ),
        ])

        items.append(dbc.AccordionItem(body, title=title))

    return html.Div([
        *header_items,
        dbc.Accordion(items, start_collapsed=True, flush=True),
    ])
