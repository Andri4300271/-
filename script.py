import os, io, re, requests, time, json
from datetime import datetime
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# --- КОНФІГУРАЦІЯ (Налаштування середовища) ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
URL_SITE = "https://poweron.loe.lviv.ua"
MEMORY_FILE = "last_memory.txt"

# --- РОБОТА З ПАМ'ЯТТЮ (Збереження стану) ---
def load_memory():
    """Завантажує історію оновлень та структуровані розклади по датах"""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"last_time": "", "group": "", "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(last_time, group, msg_ids, last_imgs, hours_by_date, last_dates):
    """Зберігає поточний стан бота у файл JSON"""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "last_time": last_time, "group": group, "msg_ids": msg_ids, 
            "last_imgs": last_imgs, "hours_by_date": hours_by_date, "last_dates": last_dates
        }, f, ensure_ascii=False)

# --- МАТЕМАТИЧНІ ОБЧИСЛЕННЯ (Розрахунок тривалості) ---
def calculate_duration(start, end):
    """Рахує тривалість періоду (напр. 00:00 - 06:00 = 6 г. 00 х.)"""
    try:
        fmt = "%H:%M"
        end_proc = "23:59" if end == "24:00" else end
        t1, t2 = datetime.strptime(start, fmt), datetime.strptime(end_proc, fmt)
        diff = t2 - t1
        s = diff.total_seconds()
        if end == "24:00": s += 60
        return f"{int(s // 3600)} г. {int((s % 3600) // 60)} х."
    except: return ""

# --- ВІЗУАЛІЗАЦІЯ ЗМІН (Логіка підкреслення <u>) ---
def format_row(s, e, dur, old_data, is_new_date):
    """Формує рядок відключення. Підкреслює години та тривалість, якщо є зміни."""
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

# --- ПАРСИНГ ТА РОЗРАХУНОК (Текст повідомлення) ---
def extract_group_info(text_block, group, old_data=None):
    """Витягує дані групи та додає блоки 'світла' з підкресленням змін часу."""
    if not group: return "", {}
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    
    current_data = {"periods": [], "light_before": None, "light_after_last": None}
    is_new_date = old_data is None

    if match:
        content = match.group(1).strip()
        if "Електроенергія є." in content and "немає" not in content:
            return "✅ <b>Електроенергія є.</b>", current_data
        
        all_matches = re.findall(r"(\d{2}:\d{2}) до (\d{2}:\d{2})", content)
        for s, e in all_matches:
            current_data["periods"].append({"start": s, "end": e, "dur": calculate_duration(s, e), "light_after": None})

        if current_data["periods"]:
            res_lines = ["⚠️ <b>Планове відключення:</b>"]
            
            # 🌑 Початок доби (від 00:00)
            first_s = current_data["periods"][0]["start"]
            if first_s != "00:00":
                l_dur = calculate_duration("00:00", first_s)
                current_data["light_before"] = l_dur
                old_l = old_data.get("light_before") if old_data else None
                l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
                res_lines.append(f"          💡  <i>{l_disp}</i>")
            
            prev_end = None
            for i, p in enumerate(current_data["periods"]):
                # 💡 Світло між блоками відключень
                if prev_end:
                    l_dur = calculate_duration(prev_end, p["start"])
                    current_data["periods"][i-1]["light_after"] = l_dur
                    old_l = None
                    if old_data and i-1 < len(old_data["periods"]):
                        old_l = old_data["periods"][i-1].get("light_after")
                    l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
                    res_lines.append(f"          💡  <i>{l_disp}</i>")
                
                res_lines.append(format_row(p["start"], p["end"], p["dur"], old_data, is_new_date))
                prev_end = p["end"]
            
            # 🌕 Кінець доби (до 24:00)
            last_e = current_data["periods"][-1]["end"]
            if last_e != "24:00":
                l_dur = calculate_duration(last_e, "24:00")
                current_data["light_after_last"] = l_dur
                old_l = old_data.get("light_after_last") if old_data else None
                l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
                res_lines.append(f"          💡  <i>{l_disp}</i>")
                
            return "\n".join(res_lines), current_data
    return "", current_data

# --- ОЧИЩЕННЯ ЧАТУ (Видалення повідомлень) ---
def clear_chat_5(msg_ids):
    """Видаляє графіки бота та останні 5 повідомлень у чаті."""
    print("🧹 [Дія] Початок очищення чату (графіки + останні повідомлення)...")
    try:
        for mid in msg_ids:
            requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
        r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': '.'}).json()
        last_id = r.get('result', {}).get('message_id')
        if last_id:
            for i in range(last_id, last_id - 6, -1):
                requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
    except Exception as e: print(f"⚠️ [Помилка] Під час очищення чату: {e}")

# --- ГОЛОВНА ЛОГІКА (Перевірка та оновлення) ---
def check_and_update():
    """Основна функція аналізу сайту та взаємодії з Telegram."""
    print(f"🕒 [{datetime.now().strftime('%H:%M:%S')}] Початок перевірки сайту.")
    mem = load_memory()
    last_site_time, current_group = mem.get("last_time", ""), mem.get("group", "")
    msg_ids, last_imgs = mem.get("msg_ids", []), mem.get("last_imgs", [])
    hours_by_date, last_dates = mem.get("hours_by_date", {}), mem.get("last_dates", [])
    
    user_interfered = False
    
    # 📩 ПРІОРИТЕТ: Перевірка БУДЬ-ЯКОГО тексту в Telegram до запуску браузера
    print("📩 [Дія] Перевірка нових повідомлень у Telegram...")
    try:
        res = requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1").json()
        if res.get('result'):
            upd = res['result'][-1]
            msg = upd.get('message', {}).get('text', '')
            if msg:
                print(f"💬 [Подія] Отримано текст: '{msg}'. Тригер повного оновлення.")
                user_interfered = True
                
                # Перевірка чи це команда зміни групи
                cmd = re.search(r"/(\d\.\d)", msg)
                if cmd:
                    new_group = cmd.group(1)
                    if new_group != current_group:
                        print(f"🎯 [Подія] Нова група: {new_group}. Очищення пам'яті.")
                        current_group = new_group
                        hours_by_date = {}
                
                # Підтвердження отримання
                requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={upd['update_id'] + 1}")
    except Exception as e: print(f"❌ [Помилка] Telegram API: {e}")

    # 🌐 БРАУЗЕР (Selenium)
    driver = None
    try:
        print(f"🌐 [Дія] Відкриття {URL_SITE}...")
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=390,1200")
        options.add_argument("user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        time.sleep(15)
        
        full_text = driver.find_element(By.TAG_NAME, "body").text
        found_times = re.findall(r"станом на (\d{2}:\d{2})", full_text)
        new_site_time = "|".join(found_times)
        imgs_elements = driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")
        current_imgs = [img.get_attribute("src") for img in imgs_elements]
        current_dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

        print(f"🔍 [Аналіз] На сайті: графіків {len(current_imgs)}, станом на {new_site_time}")

        # 🚀 ОБРОБКА ЗМІН
        if (new_site_time != last_site_time and new_site_time != "") or user_interfered:
            print("🚀 [Дія] Обробка розкладів...")
            new_hours_texts, new_hours_data_map = [], {}
            for i, b in enumerate(blocks):
                date_str = current_dates[i]
                old_d = hours_by_date.get(date_str)
                txt, dat = extract_group_info(b, current_group, old_d)
                new_hours_texts.append(txt)
                new_hours_data_map[date_str] = dat

            new_graph = any(d not in last_dates for d in current_dates)
            schedule_changed = any(new_hours_data_map.get(d) != hours_by_date.get(d) for d in current_dates if d in hours_by_date)
            time_only_changed = new_site_time != last_site_time and not schedule_changed and not new_graph
            
            # Будь-яке текстове повідомлення (user_interfered) робить повне оновлення ТА звук
            should_full_reset = user_interfered or schedule_changed or new_graph or time_only_changed
            sound_needed = user_interfered or schedule_changed or new_graph

            if should_full_reset:
                clear_chat_5(msg_ids)
                print(f"📤 [Дія] Відправка оновлень (Звук: {sound_needed})...")
                new_mids = []
                for i in range(len(current_imgs)):
                    date_str = current_dates[i]
                    date_disp = f"<u>{date_str}</u>" if date_str not in last_dates else date_str
                    s_time = found_times[i] if i < len(found_times) else ''
                    old_s_time = last_site_time.split('|')[i] if i < len(last_site_time.split('|')) else ''
                    time_disp = f"<u>{s_time}</u>" if time_only_changed and s_time != old_s_time else s_time
                    
                    cap = f"📅 <b>{date_disp}</b> група {current_group}\n⏱ <i>Станом на {time_disp}</i>\n{new_hours_texts[i]}"
                    img_data = requests.get(urljoin(URL_SITE, current_imgs[i])).content
                    r = requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", 
                                     data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML', 'disable_notification': not sound_needed}, 
                                     files={'photo': ('g.png', io.BytesIO(img_data))}).json()
                    mid = r.get('result', {}).get('message_id')
                    if mid: new_mids.append(mid)
                
                save_memory(new_site_time, current_group, new_mids, current_imgs, new_hours_data_map, current_dates)
                print("✅ [Успіх] Готово.")
                return True
            
            elif len(msg_ids) > len(current_imgs):
                print(f"🗑 [Дія] Видалення зайвого графіка.")
                for _ in range(len(msg_ids) - len(current_imgs)):
                    mid = msg_ids.pop(0)
                    requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
                save_memory(new_site_time, current_group, msg_ids, current_imgs, new_hours_data_map, current_dates)
        else: print("✅ [Статус] Змін немає.")

    except Exception as e: print(f"❌ [Помилка] {e}")
    finally:
        if driver: 
            print("🔌 [Дія] Закриття Selenium.")
            driver.quit()
    return False

# --- ТОЧКА ВХОДУ ---
if __name__ == "__main__":
    for cycle in range(1):
        print(f"\n--- [Цикл {cycle + 1}] ---")
        check_and_update()
        if cycle < 4:
            ###time.sleep(120)
