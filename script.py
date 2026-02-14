import os, io, re, requests, time, json
from datetime import datetime
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# --- КОНФІГУРАЦІЯ ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
URL_SITE = "https://poweron.loe.lviv.ua"
MEMORY_FILE = "last_memory.txt"
API_URL = f"https://api.telegram.org{TOKEN}"

# --- РОБОТА З ПАМ'ЯТТЮ ---
def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"group": "3.2", "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(group, msg_ids, last_imgs, hours_by_date, last_dates):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "group": group, "msg_ids": msg_ids, 
            "last_imgs": last_imgs, "hours_by_date": hours_by_date, "last_dates": last_dates
        }, f, ensure_ascii=False, indent=4)

# --- МАТЕМАТИКА ТА ПАРСИНГ ---
def calculate_duration(start, end):
    try:
        fmt = "%H:%M"
        end_proc = "23:59" if end == "24:00" else end
        t1, t2 = datetime.strptime(start, fmt), datetime.strptime(end_proc, fmt)
        diff = t2 - t1
        s = diff.total_seconds()
        if end == "24:00": s += 60
        return f"{int(s // 3600)} г. {int((s % 3600) // 60)} х."
    except: return ""

def extract_group_info(text_block, group, old_data=None):
    if not group: return "❌ Група не вказана", {}
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    current_data = {"periods": [], "is_full_light": False}
    is_new_date = old_data is None

    if match:
        content = match.group(1).strip()
        if "Електроенергія є." in content and "немає" not in content:
            current_data["is_full_light"] = True
            was_off = old_data and (len(old_data.get("periods", [])) > 0 or not old_data.get("is_full_light", True))
            status = "✅ <b><u>Електроенергія є.</u></b>" if was_off and not is_new_date else "✅ <b>Електроенергія є.</b>"
            return status, current_data
            
        all_matches = re.findall(r"(\d{2}:\d{2}) до (\d{2}:\d{2})", content)
        for s, e in all_matches:
            current_data["periods"].append({"start": s, "end": e, "dur": calculate_duration(s, e)})

        if current_data["periods"]:
            res_lines = ["⚠️ <b>Планове відключення:</b>"]
            prev_end = "00:00"
            for p in current_data["periods"]:
                if p["start"] != prev_end:
                    res_lines.append(f"          💡  <i>{calculate_duration(prev_end, p['start'])}</i>")
                res_lines.append(f"   <b>{p['start']} - {p['end']}</b>   ({p['dur']})")
                prev_end = p["end"]
            if prev_end != "24:00":
                res_lines.append(f"          💡  <i>{calculate_duration(prev_end, '24:00')}</i>")
            return "\n".join(res_lines), current_data
    return "❌ Дані відсутні", current_data

# --- ОЧИЩЕННЯ ЧАТУ ---
def clear_chat_all(msg_ids):
    print(f"🧹 [Очищення] Видаляємо {len(msg_ids)} повідомлень бота...")
    for mid in msg_ids:
        requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
    
    print("🧹 [Очищення] Видалення текстових запитів користувача...")
    r_temp = requests.post(f"{API_URL}/sendMessage", data={'chat_id': CHAT_ID, 'text': '...'}).json()
    if r_temp.get('ok'):
        last_id = r_temp['result']['message_id']
        for i in range(last_id, last_id - 10, -1):
            requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
    time.sleep(1) # Коротке очікування для стабільності

# --- ГОЛОВНА ЛОГІКА ---
def check_and_update():
    mem = load_memory()
    current_group = mem.get("group", "3.2")
    msg_ids = mem.get("msg_ids", [])
    hours_by_date = mem.get("hours_by_date", {})
    last_dates = mem.get("last_dates", [])
    last_imgs = mem.get("last_imgs", [])
    
    user_req = False
    try:
        resp = requests.get(f"{API_URL}/getUpdates?offset=-1&limit=5").json()
        if resp.get('result'):
            for upd in resp['result']:
                msg = upd.get('message', {})
                txt = msg.get('text', '')
                if txt:
                    user_req = True
                    print(f"📩 [Запит] Отримано текст: '{txt}'")
                    cmd = re.search(r"(\d\.\d)", txt)
                    if cmd: 
                        current_group = cmd.group(1)
                        hours_by_date = {}
                requests.get(f"{API_URL}/getUpdates?offset={upd['update_id'] + 1}")
    except: pass

    driver = None
    try:
        options = Options()
        options.add_argument("--headless=new")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        print(f"🌐 [Браузер] Перевірка {URL_SITE}...")
        time.sleep(15)
        
        full_text = driver.find_element(By.TAG_NAME, "body").text
        found_times = re.findall(r"станом на (\d{2}:\d{2})", full_text)
        current_imgs = [img.get_attribute("src") for img in driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")]
        current_dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

        if not current_dates: return

        new_hours_map = {}
        for i in range(len(current_dates)):
            d_str = current_dates[i]
            txt, dat = extract_group_info(blocks[i] if i < len(blocks) else "", current_group, hours_by_date.get(d_str))
            dat.update({"site_time": found_times[i] if i < len(found_times) else "00:00", "msg": txt})
            new_hours_map[d_str] = dat

        schedule_changed = False
        time_or_link_changed = False
        new_appeared = any(d not in last_dates for d in current_dates)
        
        for i, d in enumerate(current_dates):
            if d in hours_by_date:
                if new_hours_map[d]["periods"] != hours_by_date[d]["periods"] or \
                   new_hours_map[d]["is_full_light"] != hours_by_date[d].get("is_full_light"):
                    schedule_changed = True
                elif new_hours_map[d]["site_time"] != hours_by_date[d].get("site_time") or \
                     (i < len(last_imgs) and current_imgs[i] != last_imgs[i]):
                    time_or_link_changed = True

        should_repost = user_req or schedule_changed or new_appeared or not msg_ids or len(msg_ids) != len(current_dates)

        if should_repost:
            clear_chat_all(msg_ids)
            print(f"🚀 [Надсилання] Публікація {len(current_dates)} графіків...")
            new_mids = []
            for i in range(len(current_dates)):
                if i >= len(current_imgs): break
                d_str = current_dates[i]
                body = f"📅 <b>{d_str}</b> група {current_group}\n⏱ <i>Станом на {new_hours_map[d_str]['site_time']}</i>\n"
                body += f"<a href='{current_imgs[i]}'>Графік відключень.</a>\n\n{new_hours_map[d_str]['msg']}"
                
                r = requests.post(f"{API_URL}/sendMessage", data={'chat_id': CHAT_ID, 'text': body, 'parse_mode': 'HTML'}).json()
                print(f"DEBUG SEND: {r}") # ПОВНИЙ ВИВІД ВІДПОВІДІ ТЕЛЕГРАМ
                
                if r.get('ok'):
                    new_mids.append(r['result']['message_id'])
                else:
                    print(f"❌ ПОМИЛКА TELEGRAM: {r.get('description')}")
            
            save_memory(current_group, new_mids, current_imgs, new_hours_map, current_dates)

        elif time_or_link_changed:
            print("✏️ [Редагування] Оновлення тексту...")
            for i in range(len(current_dates)):
                if i >= len(msg_ids): break
                d_str = current_dates[i]
                body = f"📅 <b>{d_str}</b> група {current_group}\n⏱ <i>Станом на {new_hours_map[d_str]['site_time']}</i>\n"
                body += f"<a href='{current_imgs[i]}'>Графік відключень.</a>\n\n{new_hours_map[d_str]['msg']}"
                r_edit = requests.post(f"{API_URL}/editMessageText", data={'chat_id': CHAT_ID, 'message_id': msg_ids[i], 'text': body, 'parse_mode': 'HTML'}).json()
                print(f"DEBUG EDIT: {r_edit}")
            save_memory(current_group, msg_ids, current_imgs, new_hours_map, current_dates)
        else:
            print("✅ [Статус] Без змін.")

    except Exception as e: print(f"💥 Критична помилка: {e}")
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    for cycle in range(1):
        print(f"\n--- [Цикл {cycle + 1}] ---")
        check_and_update()
        if cycle < 6: time.sleep(1)
