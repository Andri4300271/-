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
                data = json.load(f)
                return data
        except: pass
    return {"group": "3.2", "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(group, msg_ids, last_imgs, hours_by_date, last_dates):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "group": group, "msg_ids": msg_ids, 
            "last_imgs": last_imgs, "hours_by_date": hours_by_date, "last_dates": last_dates
        }, f, ensure_ascii=False, indent=4)

# --- МАТЕМАТИЧНІ ОБЧИСЛЕННЯ ---
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

# --- ВІЗУАЛІЗАЦІЯ ЗМІН ---
def format_row(s, e, dur, old_data, is_new_date):
    if is_new_date or not old_data or 'periods' not in old_data:
        return f"   <b>{s} - {e}</b>   ({dur})"
    old_periods = old_data['periods']
    exact_match = any(p['start'] == s and p['end'] == e and p['dur'] == dur for p in old_periods)
    if not exact_match:
        s_disp = f"<u>{s}</u>" if not any(p['start'] == s for p in old_periods) else s
        e_disp = f"<u>{e}</u>" if not any(p['end'] == e for p in old_periods) else e
        return f"   <b>{s_disp} - {e_disp}</b>   (<u>{dur}</u>)"
    return f"   <b>{s} - {e}</b>   ({dur})"

# --- ПАРСИНГ ---
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
            was_full_light = old_data.get("is_full_light", False) if old_data else False
            header = "⚠️ <b><u>Планове відключення:</u></b>" if was_full_light and not is_new_date else "⚠️ <b>Планове відключення:</b>"
            res_lines = [header]
            prev_end = "00:00"
            for p in current_data["periods"]:
                if p["start"] != prev_end:
                    res_lines.append(f"          💡  <i>{calculate_duration(prev_end, p['start'])}</i>")
                res_lines.append(format_row(p["start"], p["end"], p["dur"], old_data, is_new_date))
                prev_end = p["end"]
            if prev_end != "24:00":
                res_lines.append(f"          💡  <i>{calculate_duration(prev_end, '24:00')}</i>")
            return "\n".join(res_lines), current_data
    return "❌ Дані відсутні", current_data

# --- ОЧИЩЕННЯ ---
def clear_chat_all(msg_ids):
    print("🧹 [Дія] Очищення чату...")
    try:
        if msg_ids:
            for mid in msg_ids:
                requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
        r = requests.post(f"{API_URL}/sendMessage", data={'chat_id': CHAT_ID, 'text': 'Оновлення...⏳'}).json()
        last_id = r.get('result', {}).get('message_id')
        if last_id:
            for i in range(last_id, last_id - 10, -1):
                requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
    except: pass

# --- ГОЛОВНА ЛОГІКА ---
def check_and_update():
    mem = load_memory()
    current_group = mem.get("group", "3.2")
    msg_ids = mem.get("msg_ids", [])
    hours_by_date = mem.get("hours_by_date", {})
    last_dates = mem.get("last_dates", [])
    last_imgs = mem.get("last_imgs", [])
    
    # 1. Перевірка команд (ЗАВЖДИ викликає оновлення)
    user_req = False
    try:
        updates = requests.get(f"{API_URL}/getUpdates?offset=-1&limit=5").json()
        if updates.get('result'):
            for upd in updates['result']:
                txt = upd.get('message', {}).get('text', '')
                if txt:
                    user_req = True # Будь-який текст — сигнал до дії
                    print(f"📩 [Запит] Команда: {txt}")
                    cmd = re.search(r"(\d\.\d)", txt)
                    if cmd: 
                        new_g = cmd.group(1)
                        if new_g != current_group:
                            print(f"🎯 [Група] Нова група: {new_g}")
                            current_group, hours_by_date = new_g, {}
                requests.get(f"{API_URL}/getUpdates?offset={upd['update_id'] + 1}")
    except: pass

    # 2. Selenium
    driver = None
    try:
        print(f"🌐 [Браузер] Перевірка {URL_SITE}...")
        options = Options()
        options.add_argument("--headless=new")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
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

        # Аналіз змін на сайті
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

        # --- КРИТЕРІЙ ДІЇ ---
        # 1. Якщо був запит користувача — ЗАВЖДИ видаляємо і надсилаємо заново
        if user_req:
            print("🚀 [Дія] Пріоритетне надсилання (за запитом користувача)...")
            clear_chat_all(msg_ids)
            new_mids = []
            for i in range(len(current_dates)):
                if i >= len(current_imgs): break
                d_str = current_dates[i]
                body = f"📅 <b>{d_str}</b> група {current_group}\n⏱ <i>Станом на {new_hours_map[d_str]['site_time']}</i>\n"
                body += f"<a href='{current_imgs[i]}'> Графік відключень.</a>\n\n{new_hours_map[d_str]['msg']}"
                r = requests.post(f"{API_URL}/sendMessage", data={'chat_id': CHAT_ID, 'text': body, 'parse_mode': 'HTML'}).json()
                if r.get('result'): new_mids.append(r['result']['message_id'])
            save_memory(current_group, new_mids, current_imgs, new_hours_map, current_dates)

        # 2. Якщо змінився графік або з'явився новий день — видаляємо і надсилаємо
        elif schedule_changed or new_appeared or not msg_ids or len(msg_ids) != len(current_dates):
            print("🚀 [Дія] Оновлення графіку (зміни на сайті)...")
            clear_chat_all(msg_ids)
            new_mids = []
            for i in range(len(current_dates)):
                if i >= len(current_imgs): break
                d_str = current_dates[i]
                date_h = f"<u>{d_str}</u>" if d_str not in last_dates else d_str
                body = f"📅 <b>{date_h}</b> група {current_group}\n⏱ <i>Станом на {new_hours_map[d_str]['site_time']}</i>\n"
                body += f"<a href='{current_imgs[i]}'> Графік відключень.</a>\n\n{new_hours_map[d_str]['msg']}"
                r = requests.post(f"{API_URL}/sendMessage", data={'chat_id': CHAT_ID, 'text': body, 'parse_mode': 'HTML'}).json()
                if r.get('result'): new_mids.append(r['result']['message_id'])
            save_memory(current_group, new_mids, current_imgs, new_hours_map, current_dates)

        # 3. Тільки зміна часу/лінку — редагуємо
        elif time_or_link_changed:
            print("✏️ [Дія] Редагування часу...")
            for i in range(len(current_dates)):
                if i >= len(msg_ids): break
                d_str = current_dates[i]
                t_disp = f"<u>{new_hours_map[d_str]['site_time']}</u>" if new_hours_map[d_str]['site_time'] != hours_by_date.get(d_str, {}).get('site_time') else new_hours_map[d_str]['site_time']
                body = f"📅 <b>{d_str}</b> група {current_group}\n⏱ <i>Станом на {t_disp}</i>\n"
                body += f"<a href='{current_imgs[i]}'> Графік відключень.</a>\n\n{new_hours_map[d_str]['msg']}"
                requests.post(f"{API_URL}/editMessageText", data={'chat_id': CHAT_ID, 'message_id': msg_ids[i], 'text': body, 'parse_mode': 'HTML'})
            save_memory(current_group, msg_ids, current_imgs, new_hours_map, current_dates)
        else:
            print("✅ [Статус] Без змін.")

    except Exception as e: print(f"❌ Помилка: {e}")
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    for cycle in range(1):
        print(f"\n--- [Цикл {cycle + 1} з 7] ---")
        check_and_update()
        if cycle < 6: time.sleep(1)
