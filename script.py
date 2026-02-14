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
# Формуємо базовий URL один раз. ОБОВ'ЯЗКОВО з /bot
API_URL = f"https://api.telegram.org{TOKEN}"

# --- РОБОТА З ПАМ'ЯТТЮ ---
def load_memory():
    """Завантажує попередній стан бота."""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Перевірка наявності ключів
                keys = ["group", "msg_ids", "last_imgs", "hours_by_date", "last_dates"]
                for k in keys: 
                    if k not in data: data[k] = []
                return data
        except: pass
    return {"group": "3.2", "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(group, msg_ids, last_imgs, hours_by_date, last_dates):
    """Зберігає поточний стан у JSON."""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "group": group, "msg_ids": msg_ids, 
            "last_imgs": last_imgs, "hours_by_date": hours_by_date, "last_dates": last_dates
        }, f, ensure_ascii=False, indent=4)

# --- МАТЕМАТИКА ТА ФОРМАТУВАННЯ ---
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

def format_row(s, e, dur, old_data, is_new_date):
    """Підкреслює зміни в конкретних годинах."""
    if is_new_date or not old_data or 'periods' not in old_data:
        return f"   <b>{s} - {e}</b>   ({dur})"
    old_periods = old_data['periods']
    exact_match = any(p['start'] == s and p['end'] == e and p['dur'] == dur for p in old_periods)
    if not exact_match:
        s_disp = f"<u>{s}</u>" if not any(p['start'] == s for p in old_periods) else s
        e_disp = f"<u>{e}</u>" if not any(p['end'] == e for p in old_periods) else e
        d_disp = f"<u>{dur}</u>" if not any(p['dur'] == dur for p in old_periods) else dur
        return f"   <b>{s_disp} - {e_disp}</b>   ({d_disp})"
    return f"   <b>{s} - {e}</b>   ({dur})"

# --- ПАРСИНГ ---
def extract_group_info(text_block, group, old_data=None):
    """Шукає дані групи, формує текст і підкреслює зміни 'Електроенергія є'."""
    if not group: return "Група не задана", {}
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    current_data = {"periods": [], "is_full_light": False}
    is_new_date = old_data is None

    if match:
        content = match.group(1).strip()
        # Якщо світло є на весь день
        if "Електроенергія є." in content and "немає" not in content:
            current_data["is_full_light"] = True
            was_off = old_data and (len(old_data.get("periods", [])) > 0 or not old_data.get("is_full_light", True))
            txt = "✅ <b><u>Електроенергія є.</u></b>" if was_off and not is_new_date else "✅ <b>Електроенергія є.</b>"
            return txt, current_data
            
        # Якщо є графік
        all_matches = re.findall(r"(\d{2}:\d{2}) до (\d{2}:\d{2})", content)
        for s, e in all_matches:
            current_data["periods"].append({"start": s, "end": e, "dur": calculate_duration(s, e)})

        if current_data["periods"]:
            was_full_light = old_data.get("is_full_light", False) if old_data else False
            header = "⚠️ <b><u>Планове відключення:</u></b>" if was_full_light and not is_new_date else "⚠️ <b>Планове відключення:</b>"
            res_lines = [header]
            
            # (Логіка 💡 періодів аналогічна попереднім версіям)
            prev_end = "00:00"
            for i, p in enumerate(current_data["periods"]):
                if p["start"] != prev_end:
                    l_dur = calculate_duration(prev_end, p["start"])
                    res_lines.append(f"          💡  <i>{l_dur}</i>")
                res_lines.append(format_row(p["start"], p["end"], p["dur"], old_data, is_new_date))
                prev_end = p["end"]
            if prev_end != "24:00":
                res_lines.append(f"          💡  <i>{calculate_duration(prev_end, '24:00')}</i>")
            
            return "\n".join(res_lines), current_data
    return "❌ Дані відсутні", current_data

# --- ОЧИЩЕННЯ ---
def clear_chat_all(msg_ids):
    """Повне видалення повідомлень."""
    print("🧹 [Дія] Повне видалення старих повідомлень...")
    try:
        # Видаляємо старі повідомлення бота
        if msg_ids:
            for mid in msg_ids:
                requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
        # Видаляємо останні текстові команди
        r = requests.post(f"{API_URL}/sendMessage", data={'chat_id': CHAT_ID, 'text': 'Оновлюю графік...⏳'}).json()
        last_id = r.get('result', {}).get('message_id')
        if last_id:
            for i in range(last_id, last_id - 8, -1):
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
    
    # 1. Перевірка команд
    user_req = False
    try:
        updates = requests.get(f"{API_URL}/getUpdates?offset=-1&limit=5").json()
        if updates.get('result'):
            for upd in updates['result']:
                txt = upd.get('message', {}).get('text', '')
                if txt:
                    user_req = True
                    print(f"📩 [Telegram] Отримано запит: {txt}")
                    cmd = re.search(r"(\d\.\d)", txt)
                    if cmd: 
                        new_g = cmd.group(1)
                        if new_g != current_group:
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
        time.sleep(15) # Даємо сайту час провантажитися
        
        full_text = driver.find_element(By.TAG_NAME, "body").text
        found_times = re.findall(r"станом на (\d{2}:\d{2})", full_text)
        current_imgs = [img.get_attribute("src") for img in driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")]
        current_dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

        if not current_dates:
            print("🛑 [Помилка] Дані на сайті не знайдено.")
            return

        new_hours_map = {}
        for i in range(len(current_dates)):
            d_str = current_dates[i]
            txt, dat = extract_group_info(blocks[i] if i < len(blocks) else "", current_group, hours_by_date.get(d_str))
            dat.update({"site_time": found_times[i] if i < len(found_times) else "00:00", "msg": txt})
            new_hours_map[d_str] = dat

        # --- АНАЛІЗ ЗМІН ---
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

        # --- ДІЯ: ПОВНЕ ОНОВЛЕННЯ ---
        # Якщо немає повідомлень у пам'яті (порожній msg_ids), бот ПОВИНЕН їх надіслати.
        if user_req or schedule_changed or new_appeared or not msg_ids or len(msg_ids) != len(current_dates):
            clear_chat_all(msg_ids)
            print("🚀 [Дія] Надсилання оновлених графіків...")
            new_mids = []
            for i in range(len(current_dates)):
                d_str = current_dates[i]
                if i >= len(current_imgs): break
                is_new = d_str not in last_dates
                date_h = f"<u>{d_str}</u>" if is_new else d_str
                
                body = f"📅 <b>{date_h}</b> група {current_group}\n⏱ <i>Станом на {new_hours_map[d_str]['site_time']}</i>\n"
                body += f"<a href='{current_imgs[i]}'> Графік відключень.</a>\n\n"
                body += new_hours_map[d_str]["msg"]
                
                r = requests.post(f"{API_URL}/sendMessage", data={'chat_id': CHAT_ID, 'text': body, 'parse_mode': 'HTML'}).json()
                print(f"📩 Статус відправки: {r.get('ok')}") # Лог для перевірки
                mid = r.get('result', {}).get('message_id')
                if mid: new_mids.append(mid)
            save_memory(current_group, new_mids, current_imgs, new_hours_map, current_dates)

        # --- ДІЯ: РЕДАГУВАННЯ ---
        elif time_or_link_changed:
            print("✏️ [Дія] Редагування існуючих повідомлень...")
            for i in range(len(current_dates)):
                if i >= len(msg_ids): break
                d_str = current_dates[i]
                old_time = hours_by_date.get(d_str, {}).get("site_time")
                new_time = new_hours_map[d_str]["site_time"]
                time_disp = f"<u>{new_time}</u>" if new_time != old_time else new_time
                
                new_text = f"📅 <b>{d_str}</b> група {current_group}\n⏱ <i>Станом на {time_disp}</i>\n"
                new_text += f"<a href='{current_imgs[i]}'> Графік відключень.</a>\n\n"
                new_text += new_hours_map[d_str]["msg"]
                
                requests.post(f"{API_URL}/editMessageText", data={'chat_id': CHAT_ID, 'message_id': msg_ids[i], 'text': new_text, 'parse_mode': 'HTML'})
            save_memory(current_group, msg_ids, current_imgs, new_hours_map, current_dates)
        else:
            print("✅ [Статус] Змін немає.")

    except Exception as e: print(f"❌ Помилка: {e}")
    finally:
        if driver: driver.quit()

# --- ЗАПУСК ---
if __name__ == "__main__":
    for cycle in range(1):
        print(f"\n--- [Цикл {cycle + 1} з 7] ---")
        check_and_update()
        if cycle < 6: time.sleep(1)
