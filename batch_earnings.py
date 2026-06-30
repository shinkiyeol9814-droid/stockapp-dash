import os
import json
import asyncio
import re
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION", "")
TARGET_CHANNEL = "https://t.me/darthacking" 

DATA_FILE = "data/earnings/earnings_data.json"
SYNC_FILE = "data/earnings/last_sync.txt" # 💡 마지막 수집 시간을 기억할 메모장!

def calc_growth(cur_val, prev_val):
    try:
        cur = int(cur_val.replace(',', ''))
        prev = int(prev_val.replace(',', ''))
        if prev > 0 and cur > 0:
            val = ((cur / prev) - 1) * 100
            return f"+{val:.1f}%" if val > 0 else f"{val:.1f}%"
        elif prev < 0 and cur > 0: return "흑전"
        elif prev > 0 and cur < 0: return "적전"
        elif prev <= 0 and cur <= 0: return "적지"
        return "-"
    except:
        return "-"

def parse_earnings_text(text):
    if "기업명:" not in text or "영업익" not in text:
        return None
        
    data = {}
    try:
        time_match = re.search(r'(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})', text)
        data['발표시간'] = time_match.group(1) if time_match else datetime.now().strftime('%Y.%m.%d %H:%M:%S')
        
        corp_match = re.search(r'기업명:\s*([^\(]+).*?([A-Z0-9]{6})', text)
        if corp_match:
            data['종목명'] = corp_match.group(1).strip()
            data['코드'] = corp_match.group(2).strip()
        else:
            return None
            
        report_match = re.search(r'보고서명:\s*(.+)', text)
        data['보고서명'] = report_match.group(1).strip() if report_match else ""
        data['잠정여부'] = "잠정공시" if "잠정" in data['보고서명'] else "확정공시"
        
        # 💡 [삭제됨] 너무 깐깐했던 기존 quarter_match 정규식 삭제!
        
        rev_match = re.search(r'매출액\s*:\s*([-+]?[\d,]+)억\s*(?:\(예상치\s*:\s*([-+]?[\d,]+)[^\/]*\/\s*([+-]?\s*\d+)%\))?', text)
        if rev_match:
            data['매출액'] = rev_match.group(1)
            data['매출괴리율'] = rev_match.group(3).replace(' ', '') if rev_match.group(3) else ""
        else:
            data['매출액'] = "-"
            data['매출괴리율'] = ""
        
        op_match = re.search(r'영업익\s*:\s*([-+]?[\d,]+)억\s*(?:\(예상치\s*:\s*([-+]?[\d,]+)[^\/]*\/\s*([+-]?\s*\d+)%\))?', text)
        if op_match:
            data['영업익'] = op_match.group(1)
            data['예상영업익'] = op_match.group(2) if op_match.group(2) else ""
            raw_gap = op_match.group(3)
            data['괴리율'] = raw_gap.replace(' ', '') if raw_gap else ""
            
            if data['예상영업익'] and data['괴리율']:
                try:
                    diff = int(data['괴리율'])
                    if diff >= 10: data['서프_상태'] = "🔥 어닝서프라이즈"
                    elif diff > 0: data['서프_상태'] = "🔥 컨센상회"
                    elif diff <= -10: data['서프_상태'] = "❄️ 어닝쇼크"
                    elif diff < 0: data['서프_상태'] = "💧 컨센하회"
                    else: data['서프_상태'] = "✅ 컨센부합"
                except:
                    data['서프_상태'] = "💡 데이터오류"
            else:
                data['서프_상태'] = "💡 컨센없음"
        else:
            data['영업익'] = "-"
            data['서프_상태'] = "N/A"

        # 💡 [통합됨] 최근 실적 추이 블록에서 분기 + YoY + QoQ 한방에 추출
        data['해당분기'] = "분기미상" # 기본값
        data['YoY'] = ""
        data['QoQ'] = ""
        
        history_match = re.search(r'\*\*최근 실적 추이\*\*\s*(.+?)(?:공시링크|$)', text, re.DOTALL)
        if history_match:
            history_text = history_match.group(1)
            
            # 💡 [핵심] 숫자나 '억' 글자 유무에 상관없이, 
            # 이 구역에서 가장 먼저 등장하는 '2025.4Q' 같은 패턴을 무조건 잡아냅니다!
            q_match = re.search(r'(\d{4}\.\d[Qq])', history_text)
            if q_match:
                data['해당분기'] = q_match.group(1).upper()
                
            # YoY, QoQ 계산 로직은 기존 유지
            hist_lines = re.findall(r'(\d{4}\.\d[Qq])\s+([-+]?[\d,]+)억\s*/\s*([-+]?[\d,]+)억', history_text)
            if len(hist_lines) >= 2:
                data['QoQ'] = calc_growth(hist_lines[0][2], hist_lines[1][2])
            if len(hist_lines) >= 5:
                data['YoY'] = calc_growth(hist_lines[0][2], hist_lines[4][2])

        data['원문'] = text
        return data
    except Exception as e:
        print(f"파싱 에러 발생: {e}")
        return None

async def main():
    print("=== 실적 스크리닝 수집 시작 ===")
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    earnings_dict = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            old_list = json.load(f)
            earnings_dict = {item['코드']: item for item in old_list}

    client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH, connection_retries=5, timeout=20)
    await client.start()
    
    now_kst = datetime.utcnow() + timedelta(hours=9)
    target_time = datetime(now_kst.year, 1, 1) # 왕초보 기본값: 올해 1월 1일
    
    # 💡 [핵심 추가] 이전 수집 기록이 있다면 타겟 타임을 덮어씌웁니다.
    if os.path.exists(SYNC_FILE):
        try:
            with open(SYNC_FILE, "r") as f:
                saved_time_str = f.read().strip()
                target_time = datetime.fromisoformat(saved_time_str)
                print(f"🕒 기억 불러오기 성공: {target_time.strftime('%Y-%m-%d %H:%M:%S')} 이후 신규 데이터만 가져옵니다.")
        except Exception as e:
            print(f"🕒 기억 불러오기 실패, 1월 1일부터 전체 탐색합니다. ({e})")

    new_count = 0
    current_run_seen = set()
    max_seen_time = target_time # 이번 턴에 가장 최신이었던 메시지 시간 저장용
    
    try:
        async for message in client.iter_messages(TARGET_CHANNEL, limit=None):
            msg_time_kst = message.date.replace(tzinfo=None) + timedelta(hours=9)
            
            # 제일 최신 메시지의 시간을 기록해 둡니다.
            if msg_time_kst > max_seen_time:
                max_seen_time = msg_time_kst
            
            # 💡 [조기 종료] 저장된 마지막 시간보다 오래된 메시지를 만나면 파싱 즉시 중단!
            if msg_time_kst <= target_time:
                print("🛑 기수집 구간 도달! 안전하게 탐색을 종료합니다.")
                break 
            
            if message.text:
                parsed_data = parse_earnings_text(message.text)
                if parsed_data:
                    code = parsed_data['코드']
                    if code not in current_run_seen:
                        current_run_seen.add(code)
                        earnings_dict[code] = parsed_data
                        new_count += 1
                        print(f"✅ 신규/갱신 수집: {parsed_data['종목명']} ({parsed_data.get('해당분기')}) - {msg_time_kst.strftime('%m/%d %H:%M')}")
                        
    except Exception as e:
        print(f"\n⚠️ 텔레그램 통신 중단 발생 (지금까지의 데이터를 안전하게 저장합니다): {e}\n")
        
    finally:
        if client.is_connected():
            await client.disconnect()
            
        # 💡 [핵심 추가] 이번에 새로 본 메시지가 있다면 시간 기억 업데이트!
        if max_seen_time > target_time:
            with open(SYNC_FILE, "w") as f:
                f.write(max_seen_time.isoformat())
            print(f"💾 다음 배치를 위해 수집 시점({max_seen_time.strftime('%Y-%m-%d %H:%M:%S')})을 기억했습니다.")
        
        final_list = list(earnings_dict.values())
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(final_list, f, indent=4, ensure_ascii=False)
            
        print(f"=== 수집 종료! (최신 {new_count}건 갱신, 총 {len(final_list)}건 누적) ===")

if __name__ == "__main__":
    asyncio.run(main())
