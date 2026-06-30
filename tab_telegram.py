"""
tab_telegram.py — 텔레그램 채널 뷰어 tab.
"""
import re, os, asyncio, time
from datetime import timedelta
import dash_bootstrap_components as dbc
from dash import html, dcc, Input, Output, State, callback, ctx, no_update

from data_layer import ttl_cache

_URL_RE = re.compile(r"(https?://[^\s<>\"']+[^\s<>\"'.,!?)\]])")


def _get_config():
    try:
        api_id   = os.environ.get("TELEGRAM_API_ID",   "")
        api_hash = os.environ.get("TELEGRAM_API_HASH", "")
        session  = os.environ.get("TELEGRAM_SESSION",  "")
        return int(api_id) if api_id else 0, api_hash, session
    except:
        return 0, "", ""


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _fetch_dialogs(api_id, api_hash, session_str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    client  = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.start()
    dialogs = []
    try:
        async for d in client.iter_dialogs():
            if not (d.is_channel or d.is_group):
                continue
            username = getattr(d.entity, "username", None)
            dialogs.append({
                "id":         d.id,
                "name":       d.name or f"채널 {d.id}",
                "unread":     d.unread_count,
                "entity_key": username if username else str(d.id),
            })
    finally:
        await client.disconnect()
    return dialogs


async def _fetch_messages(api_id, api_hash, session_str, entity_key, limit, query=""):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.start()
    msgs = []
    try:
        try:
            entity = int(entity_key)
        except ValueError:
            entity = entity_key
        kwargs = {"limit": limit}
        if query:
            kwargs["search"] = query
        async for m in client.iter_messages(entity, **kwargs):
            kst  = m.date.replace(tzinfo=None) + timedelta(hours=9)
            item = {"time_str": kst.strftime("%m/%d %H:%M"), "text": m.text or "", "doc_name": ""}
            if m.document:
                try:
                    item["doc_name"] = m.document.attributes[0].file_name
                except:
                    item["doc_name"] = f"file_{m.id}"
            if item["text"] or item["doc_name"]:
                msgs.append(item)
    except:
        pass
    finally:
        await client.disconnect()
    return msgs


@ttl_cache(seconds=300)
def load_dialogs():
    api_id, api_hash, session = _get_config()
    if not api_id or not session:
        return None
    try:
        return _run(_fetch_dialogs(api_id, api_hash, session))
    except:
        return []


@ttl_cache(seconds=180)
def load_messages(entity_key: str, limit: int):
    api_id, api_hash, session = _get_config()
    if not api_id or not session:
        return []
    try:
        return _run(_fetch_messages(api_id, api_hash, session, entity_key, limit))
    except:
        return []


@ttl_cache(seconds=60)
def search_messages(entity_key: str, query: str, limit: int):
    api_id, api_hash, session = _get_config()
    if not api_id or not session:
        return []
    try:
        return _run(_fetch_messages(api_id, api_hash, session, entity_key, limit, query=query))
    except:
        return []


def _linkify(text: str) -> str:
    return _URL_RE.sub(
        r'<a href="\1" target="_blank" rel="noopener noreferrer" '
        r'style="color:#0088cc;word-break:break-all;">\1</a>',
        text,
    )


def _render_msg(msg: dict) -> html.Div:
    time_str = msg["time_str"]
    text     = msg["text"]
    doc_name = msg["doc_name"]

    if doc_name:
        icon    = "📄" if doc_name.lower().endswith(".pdf") else "📎"
        caption = text[:120].replace("\n", " ") if text else ""
        return html.Div([
            html.Div(f"🕐 {time_str}", style={"fontSize": "11px", "color": "#999", "marginBottom": "3px"}),
            html.Div([html.Span(f"{icon} ", style={"color": "#0088cc"}),
                      html.B(doc_name, style={"color": "#0088cc"})]),
            html.Div(caption, style={"fontSize": "12px", "color": "#666"}) if caption else None,
        ], style={"border": "1px solid #e0e0e0", "borderRadius": "8px",
                  "padding": "10px 14px", "marginBottom": "6px", "background": "#fff"})

    elif text:
        is_long = len(text) > 120
        return html.Div([
            html.Div(f"🕐 {time_str}", style={"fontSize": "11px", "color": "#999", "marginBottom": "3px"}),
            html.Div(
                dcc.Markdown(text, dangerously_allow_html=False,
                             style={"fontSize": "13px", "color": "#222", "lineHeight": "1.7",
                                    "wordBreak": "break-word"}),
            ),
        ], style={"borderLeft": "3px solid #0088cc", "background": "#f8f9fa",
                  "borderRadius": "0 8px 8px 0", "padding": "10px 14px",
                  "marginBottom": "6px"})

    return html.Div()


# ── Layout ─────────────────────────────────────────────────────────────────────
def layout():
    api_id, _, session = _get_config()
    if not api_id or not session:
        return html.Div([
            html.H5("💬 텔레그램 채널 뷰어"),
            dbc.Alert(
                "TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION 환경변수를 설정해주세요.",
                color="warning",
            ),
        ])

    # layout()에서는 network 호출 없이 뼈대만 반환.
    # 채널 목록은 init_dialogs 콜백이 비동기로 채워 넣음.
    return html.Div([
        dcc.Store(id="tg-load-trigger", data=True),  # 탭 렌더 시 init_dialogs 트리거

        dbc.Row([
            dbc.Col(html.H5("💬 텔레그램 채널 뷰어", className="mb-0"), width="auto"),
            dbc.Col(
                dbc.Button("🔄 채널 새로고침", id="tg-refresh-channels-btn",
                           color="light", size="sm", n_clicks=0),
                width="auto", className="ms-auto",
            ),
        ], className="mb-3 align-items-center"),

        dbc.Row([
            dbc.Col(
                dbc.Spinner(
                    dcc.Dropdown(
                        id="tg-channel-dropdown",
                        placeholder="채널 불러오는 중…",
                        clearable=False,
                        style={"fontSize": "13px"},
                    ),
                    color="primary", size="sm",
                ),
                width=5,
            ),
            dbc.Col(
                dbc.Input(id="tg-search-input", placeholder="검색어…", size="sm", debounce=True),
                width=3,
            ),
            dbc.Col(
                dbc.Select(
                    id="tg-limit-select",
                    options=[{"label": f"{n}개", "value": str(n)} for n in [30, 50, 100, 200]],
                    value="50",
                    size="sm",
                ),
                width=2,
            ),
            dbc.Col(
                dbc.Button("🔄 불러오기", id="tg-load-btn", color="primary",
                           size="sm", n_clicks=0),
                width="auto",
            ),
        ], className="mb-3 g-2 align-items-center"),

        html.Div(id="tg-channel-status", className="mb-2"),
        dbc.Spinner(html.Div(id="tg-messages"), color="primary"),
    ])


# ── Callbacks ───────────────────────────────────────────────────────────────────

@callback(
    Output("tg-channel-dropdown",    "options"),
    Output("tg-channel-dropdown",    "value"),
    Output("tg-channel-status",      "children"),
    Input("tg-load-trigger",         "data"),       # 탭 초기 렌더 시 1회
    Input("tg-refresh-channels-btn", "n_clicks"),   # 수동 새로고침
    prevent_initial_call=False,
)
def init_dialogs(_, n_refresh):
    if ctx.triggered_id == "tg-refresh-channels-btn":
        load_dialogs.clear()

    dialogs = load_dialogs()

    if not dialogs:
        return [], None, dbc.Alert("채널/그룹을 불러올 수 없습니다.", color="danger")

    opts = [
        {"label": f"{'🔔 ' if d['unread'] else ''}{d['name']} ({d['unread']})",
         "value": d["entity_key"]}
        for d in dialogs
    ]
    return opts, opts[0]["value"] if opts else None, html.Div()


@callback(
    Output("tg-messages", "children"),
    Input("tg-load-btn", "n_clicks"),
    State("tg-channel-dropdown", "value"),
    State("tg-search-input", "value"),
    State("tg-limit-select", "value"),
    prevent_initial_call=True,
)
def load_msgs(n_clicks, entity_key, query, limit_str):
    if not entity_key:
        return dbc.Alert("채널을 선택해주세요.", color="secondary")

    limit = int(limit_str or "50")
    msgs  = (search_messages(entity_key, query, limit)
             if query
             else load_messages(entity_key, limit))

    if not msgs:
        return dbc.Alert("메시지가 없습니다.", color="info")

    return html.Div([_render_msg(m) for m in msgs])
