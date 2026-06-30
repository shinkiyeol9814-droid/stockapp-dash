import os
import json
import asyncio
import aiohttp
import time
import requests
import urllib.parse
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timedelta
import pandas as pd
import FinanceDataReader as fdr
from telethon import TelegramClient
from telethon.sessions import StringSession
from google import genai

# 환경 변수 설정
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY_A", "")

client_ai = genai.Client(api_key=GEMINI_KEY)

def _get_krx_listing():
    """KRX 전체 종목 리스트 — fdr.StockListing('KRX') 실패 시 KOSPI+KOSDAQ 폴백"""
    try:
        df = fdr.StockListing('KRX')
        if not df.empty and 'Marcap' in df.columns:
            return df
    except Exception as e:
        print(f"⚠️ fdr.StockListing('KRX') 실패: {e}")
    print("📌 KOSPI + KOSDAQ 분리 수집으로 폴백합니다...")
    df_kospi  = fdr.StockListing('KOSPI')
    df_kosdaq = fdr.StockListing('KOSDAQ')
    return pd.concat([df_kospi, df_kosdaq], ignore_index=True)

def get_high_stocks():
    print("데이터 수집 및 필터링 시작...")
    df = _get_krx_listing()

    df['Marcap']      = pd.to_numeric(df['Marcap'],      errors='coerce').fillna(0)
    df['Close']       = pd.to_numeric(df['Close'],       errors='coerce').fillna(0)
    df['Volume']      = pd.to_numeric(df['Volume'],      errors='coerce').fillna(0)
    df['ChagesRatio'] = pd.to_numeric(df['ChagesRatio'], errors='coerce').fillna(0)

    df = df[(df['Marcap'] >= 50_000_000_000) & (df['Close'] >= 1000)].copy()
    df = df[df['ChagesRatio'] > 0.0]
    candidates = df.sort_values('ChagesRatio', ascending=False)
    results = []

    start_date = (datetime.today() - timedelta(days=365)).strftime('%Y-%m-%d')
    print(f"필터 통과 {len(candidates)}개 종목 신고가 정밀 연산 중...")
    for row in candidates.itertuples():
        try:
            hist = fdr.DataReader(row.Code, start_date)
            if hist.empty or len(hist) < 20: continue
            
            # 오늘을 제외한 '과거' 데이터만 분리하여 매물대 계산 (윗꼬리 왜곡 방지)
            past_hist = hist.iloc[:-1]
            if past_hist.empty: continue
            
            # 과거 기간별 최고 '종가' (매물대 저항선)
            past_max_1y = past_hist['Close'].max()
            past_max_6m = past_hist['Close'].tail(120).max()
            past_max_3m = past_hist['Close'].tail(60).max()
            
            today_close = int(hist['Close'].iloc[-1])
            
            period_flag = ""
            # 💡 [핵심 수정] 0.98(98%) 버퍼를 삭제하고, 과거 최고 종가를 '완벽하게' 넘은 녀석만 인정
            if today_close >= past_max_1y: period_flag = "1년(52주) 신고가"
            elif today_close >= past_max_6m: period_flag = "6개월 신고가"
            elif today_close >= past_max_3m: period_flag = "3개월 신고가"
            
            if period_flag:
                results.append({
                    "종목명": row.Name,
                    "코드": row.Code,
                    "현재가": today_close,
                    "시가총액": int(row.Marcap),
                    "등락률": row.ChagesRatio,
                    "돌파기간": period_flag
                })
        except Exception:
            pass
            
    return results

async def get_telegram_news(client, stock_name):
    messages_text = []
    today = datetime.now().date()
    try:
        # 찌라시 수집 개수 5개로 통제
        async for message in client.iter_messages(None, search=stock_name, limit=5):
            if message.date.date() == today and message.text:
                messages_text.append(message.text)
    except Exception as e:
        print(f"텔레그램 에러 ({stock_name}): {e}")
    return " \n".join(messages_text)

# 구글 뉴스 크롤링 완전 비동기화 (aiohttp 적용)
async def get_google_news(session, stock_name):
    query = f'"{stock_name}" 특징주 OR 주가'
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
    
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as res:
            text = await res.text()
            root = ET.fromstring(text)
            
            news_titles = []
            news_markdown = []
            first_link = "" 
            
            for i, item in enumerate(root.findall('.//item')[:5]): 
                title = item.find('title').text
                link = item.find('link').text
                news_titles.append(title)
                news_markdown.append(f"- [{title}]({link})")
                if i == 0: first_link = link
                    
            ai_text = " \n".join(news_titles) if news_titles else "관련 뉴스 없음"
            ui_markdown = " \n".join(news_markdown) if news_markdown else "관련 뉴스 없음"
            return ai_text, ui_markdown, first_link
            
    except Exception as e:
        return f"뉴스 수집 에러: {e}", "관련 뉴스 없음", ""

# 텔레그램과 구글 뉴스를 동시에 비동기로 긁어오는 워커 함수
async def fetch_stock_data(s, client_tg, session, sem):
    async with sem: # 과부하 차단(동시접속 제한)
        tg_task = get_telegram_news(client_tg, s['종목명'])
        news_task = get_google_news(session, s['종목명'])
        
        tg_text, (ai_news_text, ui_news_markdown, first_link) = await asyncio.gather(tg_task, news_task)
        
        s['최신뉴스'] = ai_news_text.split('\n')[0] if ai_news_text != "관련 뉴스 없음" else "관련 뉴스 없음"
        s['최신뉴스_링크'] = first_link 
        s['뉴스목록'] = ui_news_markdown
        s['PER'] = "조회필요"
        
        if not tg_text.strip() and ai_news_text == "관련 뉴스 없음":
            s['추정 사유'] = "시장 수급 유입 (구체적인 뉴스/찌라시 미발견)"
            return None # AI 분석 큐에서 제외
        else:
            s['추정 사유'] = "분석 대기"
            return {'name': s['종목명'], 'tg': tg_text, 'news': ai_news_text, 'ref': s}

def summarize_batch_with_gemini(batch_data, max_retries=3):
    if not batch_data: return {}

    prompt = f"""너는 냉철한 주식 분석가야. 아래 전달하는 {len(batch_data)}개 종목의 뉴스(팩트)와 텔레그램(루머) 데이터를 읽고, 각 종목이 신고가를 뚫은 핵심 모멘텀을 50자 이내로 1줄 요약해.
반드시 아래와 같이 [종목명|요약내용] 규칙의 텍스트로만 대답하고, 전달된 {len(batch_data)}개 종목을 단 하나도 빠짐없이 전부 출력해.
주의: 앞에 '1.', '-', '*' 같은 기호나 번호를 절대 붙이지 말고 오직 '종목명|요약내용' 형태로만 출력해.

[출력 예시]
삼성전자|반도체 업황 회복 및 HBM 수혜 기대
카카오|비용 절감 및 실적 개선

[분석할 데이터]
"""
    for data in batch_data:
        # 텍스트 슬라이싱으로 토큰 한도 초과 방어
        safe_news = data['news'][:500] + "..." if len(data['news']) > 300 else data['news']
        safe_tg = data['tg'][:800] + "..." if len(data['tg']) > 300 else data['tg']
        prompt += f"■ {data['name']}\n- 뉴스: {safe_news}\n- 찌라시: {safe_tg}\n\n"

    for attempt in range(max_retries):
        try:
            response = client_ai.models.generate_content(
                model='gemini-2.5-flash', 
                contents=prompt,
            )
            res_text = response.text.strip()
            reasons_dict = {}
            for line in res_text.split('\n'):
                if '|' in line:
                    parts = line.split('|', 1)
                    raw_name = parts[0].strip()
                    stock_name = re.sub(r'^[\d\.\-\*\s]+', '', raw_name).replace("[", "").replace("]", "")
                    summary = parts[1].strip().replace("[", "").replace("]", "")
                    reasons_dict[stock_name] = summary
            return reasons_dict
        except Exception as e:
            error_msg = str(e)
            
            # 💡 [최적화] Tier 1에 맞는 초고속 회복 로직
            if "503" in error_msg:
                wait_time = 3
                reason = "서버 일시적 과부하(503)"
            elif "429" in error_msg:
                wait_time = 5
                reason = "순간 토큰 초과(429)"
            else:
                wait_time = 2
                reason = f"기타 에러 ({error_msg[:30]})"
                
            print(f"    ⚠️ AI 분석 에러 (시도 {attempt+1}/{max_retries}) | {wait_time}초 대기... (사유: {reason})")

            if attempt < max_retries - 1:
                time.sleep(wait_time) 
            else:
                return None

# 💡 [핵심] 1바퀴 돌 때마다 즉시 JSON에 덮어쓰는 저장 함수
def save_incremental_json(stocks, save_dir, file_name, analysis_time, start_time, pass_num):
    m, sec = divmod(time.time() - start_time, 60)
    execution_time_str = f"진행중 (현재 {int(m)}분 {int(sec)}초)"
    
    report = {
        "analysis_time": analysis_time,
        "execution_time": execution_time_str,
        "results": stocks
    }
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)
    print(f"\n💾 [{pass_num}차 저장 완료] 데이터가 웹 대시보드에 즉시 업데이트되었습니다!")

async def main():
    start_time = time.time()
    print("=== 주도주 트래킹 배치 시작 (초고속 병렬 + 5회 패자부활전) ===")
    
    now = datetime.utcnow() + timedelta(hours=9)
    analysis_time = now.strftime("%Y-%m-%d %H:%M")
    save_dir = 'data/new_high'
    os.makedirs(save_dir, exist_ok=True)
    file_name = f"{save_dir}/newhigh_{now.strftime('%Y%m%d_%H%M')}.json"
    
    stocks = get_high_stocks()
    
    if not stocks:
        print("조건을 만족하는 신고가 종목이 없습니다.")
        return
        
    client_tg = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client_tg.start()
    
    print(f"\n⚡ {len(stocks)}개 종목 뉴스/찌라시 병렬 크롤링 시작...")
    
    analysis_queue = []
    sem = asyncio.Semaphore(15) 
    
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_stock_data(s, client_tg, session, sem) for s in stocks]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                print(f"⚠️ 수집 실패: {res}")
            elif res is not None:
                analysis_queue.append(res)
    
    await client_tg.disconnect()
    print(f"✅ 크롤링 완료. AI 분석 대상: {len(analysis_queue)}건")

    # 💡 5회 패자부활전 로직 설정
    chunk_size = 15
    MAX_PASSES = 8
    current_queue = analysis_queue

    for pass_num in range(1, MAX_PASSES + 1):
        if not current_queue:
            print(f"\n🎉 [완벽 성공] 누락/실패 없이 모든 찌라시 분석이 완료되었습니다! (총 {pass_num-1}회전)")
            break
            
        print(f"\n=============================================")
        print(f"🚀 [{pass_num}차 분석 시작] 총 {len(current_queue)}개 종목 분석 중...")
        print(f"=============================================")
        
        failed_queue = []
        
        for i in range(0, len(current_queue), chunk_size):
            chunk = current_queue[i:i+chunk_size]
            print(f"\n▶️ AI 분석 중 ({i+1}~{min(i+chunk_size, len(current_queue))}) / {len(current_queue)}개...")
            
            result_dict = summarize_batch_with_gemini(chunk)
            
            if result_dict is None:
                print(f"      ❌ [통째로 실패] 해당 구간 API 에러. 패자부활전으로 통째로 넘깁니다.")
                failed_queue.extend(chunk)
            else:
                for item in chunk:
                    stock_name = item['name']
                    if stock_name in result_dict:
                        item['ref']['추정 사유'] = result_dict[stock_name]
                        print(f"      ➡️ 추출 성공: [{stock_name}]")
                    else:
                        print(f"      ⚠️ AI 요약 누락: [{stock_name}] -> 패자부활전 대기열 추가")
                        failed_queue.append(item)
            
            time.sleep(2)

        save_incremental_json(stocks, save_dir, file_name, analysis_time, start_time, pass_num)

        current_queue = failed_queue
        
        if current_queue and pass_num < MAX_PASSES:
            print(f"\n⏳ {pass_num}차 분석 종료. 누락된 {len(current_queue)}개 종목 재도전을 위해 5초 대기합니다...")
            time.sleep(5)

    if current_queue:
        print(f"\n💀 [최종 종료] 최대 {MAX_PASSES}회 시도했으나 {len(current_queue)}개 종목은 AI가 추출하지 못했습니다.")
        for item in current_queue:
            item['ref']['추정 사유'] = "추출 누락 (수동 확인 필요)"
        save_incremental_json(stocks, save_dir, file_name, analysis_time, start_time, "최종")

    m, sec = divmod(time.time() - start_time, 60)
    execution_time_str = f"{int(m)}분 {int(sec)}초"
    print(f"\n=== ✅ 모든 분석 완전 종료. 총 소요시간: {execution_time_str} ===")

if __name__ == "__main__":
    asyncio.run(main())
