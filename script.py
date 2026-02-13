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

# --- РОБОТА З ПАМ'ЯТТЮ ---
def load_memory():
    """Завантажує стан останньої перевірки з файлу."""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                print(f"📖 [Пам'ять] Завантажено дані. Остання група: {data.get('group')}")
                return data
        except Exception as e: 
            print(f"⚠️ [Пам'ять] Помилка читання: {e}")
    print("🆕 [Пам'ять] Створення нового профілю (файл відсутній).")
    return {"group": "", "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(group, msg_ids, last_imgs, hours_by_date, last_dates):
    """Зберігає поточний стан у файл для майбутніх порівнянь."""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "group": group, "msg_ids": msg_ids, 
            "last_imgs": last_imgs, "hours_by_date": hours_by_date, "last_dates": last_dates
        }, f, ensure_ascii=False, indent=4)
    print(f"💾 [Пам'ять] Дані групи {group} збережено.")

# --- МАТЕМАТИЧНІ ОБЧИСЛЕННЯ ---
def calculate_duration(start, end):
    """Рахує різницю між часом (наприклад, 10:00 - 14:00 = 4 г. 0 х.)."""
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
    """Порівнює нові години зі старими. Якщо змінилися — підкреслює їх."""
    if is_new_date or not old_data or 'periods' not in old_data:
        return f"   <b>{s} - {e}</b>   ({dur})"
    
    old_periods = old_data['periods']
    exact_match = any(p['start'] == s and p['end'] == e and p['dur'] == dur for p in old_periods)
    
    if not exact_match:
        print(f"🔍 [Аналіз] Виявлено зміну в періоді: {s}-{e}")
        s_disp = f"<u>{s}</u>" if not any(p['start'] == s for p in old_periods) else s
        e_disp = f"<u>{e}</u>" if not any(p['end'] == e for p in old_periods) else e
        d_disp = f"<u>{dur}</u>" if not any(p['dur'] == dur for p in old_periods) else dur
        return f"   <b>{s_disp} - {e_disp}</b>   ({d_disp})"
    return f"   <b>{s} - {e}</b>   ({dur})"

# --- ПАРСИНГ ТА РОЗРАХУНОК ---
def extract_group_info(text_block, group, old_data=None):
    """Шукає блок тексту для конкретної групи та формує звіт."""
    if not group: return "", {}
    print(f"🔎 [Парсинг] Пошук даних для групи {group}...")
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    current_data = {"periods": [], "light_before": None, "light_after_last": None}
    is_new_date = old_data is None

    if match:
        content = match.group(1).strip()
        if "Електроенергія є." in content and "немає" not in content:
            print(f"✨ [Парсинг] Група {group}: Електроенергія є.")
            return "✅ <b>Електроенергія є.</b>", current_data
        
        all_matches = re.findall(r"(\d{2}:\d{2}) до (\d{2}:\d{2})", content)
        for s, e in all_matches:
            current_data["periods"].append({"start": s, "end": e, "dur": calculate_duration(s, e), "light_after": None})

        if current_data["periods"]:
            res_lines = ["⚠️ <b>Планове відключення:</b>"]
            # Світло на початку доби
            first_p = current_data["periods"][0]
            if first_p["start"] != "00:00":
                l_dur = calculate_duration("00:00", first_p["start"])
                current_data["light_before"] = l_dur
                old_l = old_data.get("light_before") if old_data else None
                l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
                res_lines.append(f"          💡  <i>{l_disp}</i>")
            
            # Цикл по періодах відключень
            prev_end = None
            for i, p in enumerate(current_data["periods"]):
                if prev_end:
                    l_dur = calculate_duration(prev_end, p["start"])
                    current_data["periods"][i-1]["light_after"] = l_dur
                    old_l = old_data["periods"][i-1].get("light_after") if old_data and i-1 < len(old_data["periods"]) else None
                    l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
                    res_lines.append(f"          💡  <i>{l_disp}</i>")
                res_lines.append(format_row(p["start"], p["end"], p["dur"], old_data, is_new_date))
                prev_end = p["end"]
            
            # Світло в кінці доби
            last_e = current_data["periods"][-1]["end"]
            if last_e != "24:00":
                l_dur = calculate_duration(last_e, "24:00")
                current_data["light_after_last"] = l_dur
                old_l = old_data.get("light_after_last") if old_data else None
                l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
                res_lines.append(f"          💡  <i>{l_disp}</i>")
            return "\n".join(res_lines), current_data
    return "Дані відсутні", current_data

# --- ОЧИЩЕННЯ ЧАТУ ---
def clear_chat_5(msg_ids):
    """Видаляє старі повідомлення бота та зачищає команди користувача."""
    print("🧹 [Дія] Початок повного очищення чату...")
    try:
        for mid in msg_ids:
            requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
        
        # Надсилання та видалення крапки для очищення команд
        r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': '.'}).json()
        last_id = r.get('result', {}).get('message_id')
        if last_id:
            for i in range(last_id, last_id - 6, -1):
                requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
        print("✅ [Дія] Чат успішно очищено.")
    except Exception as e: print(f"⚠️ [Помилка] Очищення: {e}")

# --- ГОЛОВНА ЛОГІКА ---
def check_and_update():
    """Основний процес перевірки сайту та оновлення Telegram."""
    print(f"\n🕒 [{datetime.now().strftime('%H:%M:%S')}] Старт циклу перевірки.")
    mem = load_memory()
    current_group = mem.get("group", "")
    msg_ids = mem.get("msg_ids", [])
    last_imgs = mem.get("last_imgs", [])
    hours_by_date = mem.get("hours_by_date", {})
    last_dates = mem.get("last_dates", [])
    
    user_interfered = False
    print("📩 [Дія] Перевірка команд у Telegram...")
    try:
        res = requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1&limit=10").json()
        if res.get('result'):
            for upd in res['result']:
                msg_text = upd.get('message', {}).get('text', '')
                if msg_text:
                    print(f"💬 [Текст] Отримано запит: '{msg_text}'.")
                    user_interfered = True
                    cmd = re.search(r"(\d\.\d)", msg_text) # Шукаємо групу типу 2.1
                    if cmd:
                        new_group = cmd.group(1)
                        if new_group != current_group:
                            print(f"🎯 [Зміна групи] {current_group} -> {new_group}.")
                            current_group = new_group
                            hours_by_date = {}
                requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={upd['update_id'] + 1}")
    except Exception as e: print(f"❌ [Помилка] Telegram API: {e}")

    driver = None
    try:
        print(f"🌐 [Дія] Відкриття браузера {URL_SITE}...")
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=390,1200")
        # Емуляція iPhone для отримання мобільної версії
        options.add_argument("user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        time.sleep(15) # Чекаємо рендеру скриптів
        
        full_text = driver.find_element(By.TAG_NAME, "body").text
        found_times = re.findall(r"станом на (\d{2}:\d{2})", full_text)
        imgs_elements = driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")
        current_imgs = [img.get_attribute("src") for img in imgs_elements]
        current_dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

        new_hours_data_map = {}
        print(f"📊 [Аналіз] Знайдено дат на сайті: {len(current_dates)}")
        
        for i, b in enumerate(blocks):
            if i >= len(current_dates): break
            date_str = current_dates[i]
            site_time = found_times[i] if i < len(found_times) else "00:00"
            old_d = hours_by_date.get(date_str)
            txt, dat = extract_group_info(b, current_group, old_d)
            dat["site_time"] = site_time 
            dat["full_text_msg"] = txt
            new_hours_data_map[date_str] = dat

        # Порівняння для рішення про оновлення
        any_schedule_change = False
        any_site_time_change = False
        new_graph_appeared = any(d not in last_dates for d in current_dates)

        for d in current_dates:
            if d in hours_by_date:
                if new_hours_data_map[d]["periods"] != hours_by_date[d]["periods"]:
                    any_schedule_change = True
                if new_hours_data_map[d]["site_time"] != hours_by_date[d].get("site_time"):
                    any_site_time_change = True

        should_update = user_interfered or any_schedule_change or any_site_time_change or new_graph_appeared
        sound_needed = user_interfered or any_schedule_change or new_graph_appeared

        if should_update:
            print("🚀 [Дія] Виявлено зміни! Надсилання оновлень у Telegram...")
            ###clear_chat_5(msg_ids)
            new_mids = []
            for i, date_str in enumerate(current_dates):
                if i >= len(current_imgs): break
                old_d = hours_by_date.get(date_str)
                is_new_date = date_str not in last_dates
                date_disp = f"<u>{date_str}</u>" if is_new_date else date_str
                curr_st = new_hours_data_map[date_str]["site_time"]
                old_st = old_d.get("site_time") if old_d else None
                time_disp = f"<u>{curr_st}</u>" if not is_new_date and old_st and curr_st != old_st else curr_st
                
                cap = f"📅 <b>{date_disp}</b> група {current_group}\n⏱ <i>Станом на {time_disp}</i>\n{new_hours_data_map[date_str]['full_text_msg']}"
                
                # Завантаження картинки та відправка
                img_data = requests.get(urljoin(URL_SITE, current_imgs[i])).content
                r = requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", 
                                 data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML', 'disable_notification': not sound_needed}, 
                                 files={'photo': ('g.png', io.BytesIO(img_data))}).json()
                
                mid = r.get('result', {}).get('message_id')
                if mid: new_mids.append(mid)
            
            save_memory(current_group, new_mids, current_imgs, new_hours_data_map, current_dates)
            return True
        elif len(msg_ids) > len(current_imgs):
            print("🗑 [Дія] Видалення застарілого графіка (кількість днів зменшилась).")
            for _ in range(len(msg_ids) - len(current_imgs)):
                mid = msg_ids.pop(0)
                ###requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
            save_memory(current_group, msg_ids, current_imgs, new_hours_data_map, current_dates)
        else: 
            print("✅ [Статус] Дані на сайті ідентичні збереженим. Оновлення не потрібне.")
    except Exception as e: print(f"❌ [Помилка] {e}")
    finally:
        if driver: 
            driver.quit()
            print("🔌 [Браузер] Закрито.")
    return False

if __name__ == "__main__":
    print("🤖 Бот запущено. Починаю роботу...")
    # Виконуємо 5 циклів перевірки
    for cycle in range(1):
        print(f"\n--- [Цикл {cycle + 1} з 5] ---")
        check_and_update()
        if cycle < 4:
            print("⏳ [Очікування] 120 секунд до наступної перевірки...")
            time.sleep(1)
    print("\n🏁 Роботу завершено.")
