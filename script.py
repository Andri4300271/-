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
# Обов'язково додаємо /bot перед токеном для коректної роботи API
API_URL = f"https://api.telegram.org{TOKEN}"

# --- РОБОТА З ПАМ'ЯТТЮ ---
def load_memory():
    """Завантажує попередній стан: групу, ID повідомлень, посилання на фото та години."""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    # Якщо файлу немає, повертаємо порожню структуру
    return {"group": "", "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(group, msg_ids, last_imgs, hours_by_date, last_dates):
    """Зберігає поточний стан у JSON файл для порівняння в наступному циклі."""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "group": group, "msg_ids": msg_ids, 
            "last_imgs": last_imgs, "hours_by_date": hours_by_date, "last_dates": last_dates
        }, f, ensure_ascii=False, indent=4)

# --- МАТЕМАТИЧНІ ОБЧИСЛЕННЯ ---
def calculate_duration(start, end):
    """Рахує тривалість (напр. 10:00 - 14:00 = 4 г. 0 х.). Обробляє 24:00 як 23:59 + 1хв."""
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
    """Формує рядок відключення. Якщо години або тривалість змінилися — підкреслює їх."""
    if is_new_date or not old_data or 'periods' not in old_data:
        return f"   <b>{s} - {e}</b>   ({dur})"
    
    old_periods = old_data['periods']
    # Шукаємо повний збіг (старт, кінець, тривалість)
    exact_match = any(p['start'] == s and p['end'] == e and p['dur'] == dur for p in old_periods)
    
    if not exact_match:
        # Підкреслюємо окремо старт, кінець або тривалість, якщо вони нові
        s_disp = f"<u>{s}</u>" if not any(p['start'] == s for p in old_periods) else s
        e_disp = f"<u>{e}</u>" if not any(p['end'] == e for p in old_periods) else e
        d_disp = f"<u>{dur}</u>" if not any(p['dur'] == dur for p in old_periods) else dur
        return f"   <b>{s_disp} - {e_disp}</b>   ({d_disp})"
    return f"   <b>{s} - {e}</b>   ({dur})"

# --- ПАРСИНГ ТА РОЗРАХУНОК ---
def extract_group_info(text_block, group, old_data=None):
    """Витягує дані для групи, рахує періоди світла 💡 та підкреслює зміну статусу 'Є світло'."""
    if not group: return "", {}
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    
    current_data = {"periods": [], "light_before": None, "light_after_last": None, "is_full_light": False}
    is_new_date = old_data is None

    if match:
        content = match.group(1).strip()
        
        # Перевірка статусу "Електроенергія є"
        if "Електроенергія є." in content and "немає" not in content:
            current_data["is_full_light"] = True
            # Якщо раніше були відключення, а тепер світло є — підкреслюємо весь статус
            was_off = old_data and (len(old_data.get("periods", [])) > 0 or not old_data.get("is_full_light", True))
            status = "✅ <b><u>Електроенергія є.</u></b>" if was_off and not is_new_date else "✅ <b>Електроенергія є.</b>"
            return status, current_data
            
        # Пошук періодів відключень
        all_matches = re.findall(r"(\d{2}:\d{2}) до (\d{2}:\d{2})", content)
        for s, e in all_matches:
            current_data["periods"].append({"start": s, "end": e, "dur": calculate_duration(s, e)})

        if current_data["periods"]:
            # Якщо раніше було світло, а тепер з'явився графік — підкреслюємо заголовок
            was_full_light = old_data.get("is_full_light", False) if old_data else False
            header = "⚠️ <b><u>Планове відключення:</u></b>" if was_full_light and not is_new_date else "⚠️ <b>Планове відключення:</b>"
            res_lines = [header]
            
            # --- 🌑 Початок доби (світло до першого відключення) ---
            first_p = current_data["periods"][0]
            if first_p["start"] != "00:00":
                l_dur = calculate_duration("00:00", first_p["start"])
                current_data["light_before"] = l_dur
                old_l = old_data.get("light_before") if old_data else None
                l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
                res_lines.append(f"          💡  <i>{l_disp}</i>")

            # --- 💡 Періоди всередині (між відключеннями) ---
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

            # --- 🌕 Кінець доби (світло після останнього відключення) ---
            last_e = current_data["periods"][-1]["end"]
            if last_e != "24:00":
                l_dur = calculate_duration(last_e, "24:00")
                current_data["light_after_last"] = l_dur
                old_l = old_data.get("light_after_last") if old_data else None
                l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
                res_lines.append(f"          💡  <i>{l_disp}</i>")
            
            return "\n".join(res_lines), current_data
    return "❌ Дані відсутні", current_data

# --- ОЧИЩЕННЯ ЧАТУ ---
def clear_chat_all(msg_ids):
    """Видаляє повідомлення бота та будь-які текстові повідомлення користувача (останні 10)."""
    print("🧹 [Дія] Видалення повідомлень та зачистка чату...")
    try:
        # Видаляємо старі повідомлення бота, збережені в пам'яті
        for mid in msg_ids:
            requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
        # Надсилаємо тимчасову крапку, щоб знайти останній ID, і видаляємо все навколо (тексти користувача)
        r = requests.post(f"{API_URL}/sendMessage", data={'chat_id': CHAT_ID, 'text': '.'}).json()
        last_id = r.get('result', {}).get('message_id')
        if last_id:
            for i in range(last_id, last_id - 10, -1):
                requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
    except: pass

# --- ГОЛОВНА ЛОГІКА ---
def check_and_update():
    """Центральна функція: перевірка команд, Selenium, порівняння змін, оновлення/редагування."""
    mem = load_memory()
    current_group, msg_ids = mem.get("group", ""), mem.get("msg_ids", [])
    last_imgs, hours_by_date = mem.get("last_imgs", []), mem.get("hours_by_date", {})
    last_dates = mem.get("last_dates", [])
    
    # 1. Перевірка вхідних команд (текстових повідомлень)
    user_req = False
    try:
        updates = requests.get(f"{API_URL}/getUpdates?offset=-1&limit=5").json()
        if updates.get('result'):
            for upd in updates['result']:
                txt = upd.get('message', {}).get('text', '')
                if txt:
                    user_req = True # Будь-який текст провокує очищення та перенадсилання
                    print(f"📩 [Запит] Отримано повідомлення: {txt}")
                    # Якщо формат /3.2 або просто 3.2 — змінюємо групу
                    cmd = re.search(r"(\d\.\d)", txt)
                    if cmd: 
                        new_g = cmd.group(1)
                        if new_g != current_group:
                            print(f"🎯 [Група] Зміна на {new_g}")
                            current_group, hours_by_date = new_g, {}
                # Підтверджуємо отримання, щоб не обробляти знову
                requests.get(f"{API_URL}/getUpdates?offset={upd['update_id'] + 1}")
    except: pass

    # 2. Робота з браузером Selenium
    driver = None
    try:
        print(f"🌐 [Браузер] Відкриття {URL_SITE}...")
        options = Options()
        options.add_argument("--headless=new")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        time.sleep(10) # Чекаємо завантаження динамічного контенту
        
        full_text = driver.find_element(By.TAG_NAME, "body").text
        # Парсимо час оновлення на сайті, посилання на картинки та дати
        found_times = re.findall(r"станом на (\d{2}:\d{2})", full_text)
        current_imgs = [img.get_attribute("src") for img in driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")]
        current_dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

        # Аналізуємо кожен блок дати
        new_hours_map = {}
        for i, b in enumerate(blocks):
            if i >= len(current_dates): break
            d_str = current_dates[i]
            txt, dat = extract_group_info(b, current_group, hours_by_date.get(d_str))
            dat.update({
                "site_time": found_times[i] if i < len(found_times) else "00:00", 
                "msg": txt
            })
            new_hours_map[d_str] = dat

        # --- АНАЛІЗ ТИПУ ЗМІН ---
        schedule_changed = False # Змінилися години або статус світла
        only_time_or_link_changed = False # Змінився тільки час або URL картинки
        new_appeared = any(d not in last_dates for d in current_dates) # З'явилася нова дата
        
        for i, d in enumerate(current_dates):
            if d in hours_by_date:
                # Перевірка зміни періодів або статусу 'is_full_light'
                if new_hours_map[d]["periods"] != hours_by_date[d]["periods"] or \
                   new_hours_map[d]["is_full_light"] != hours_by_date[d].get("is_full_light"):
                    schedule_changed = True
                # Перевірка зміни тільки часу або посилання на малюнок
                elif new_hours_map[d]["site_time"] != hours_by_date[d].get("site_time") or \
                     (i < len(last_imgs) and current_imgs[i] != last_imgs[i]):
                    only_time_or_link_changed = True

        # --- ВИКОНАННЯ ДІЙ ---

        # ДІЯ А: Запит користувача / Зміна графіку / Новий день / Зміна кількості повідомлень
        if user_req or schedule_changed or new_appeared or len(msg_ids) != len(current_dates):
            print("🚀 [Дія] Повне перенадсилання графіків...")
            clear_chat_all(msg_ids)
            new_mids = []
            for i, d_str in enumerate(current_dates):
                if i >= len(current_imgs): break
                is_new = d_str not in last_dates
                date_header = f"<u>{d_str}</u>" if is_new else d_str # Підкреслюємо нову дату
                
                # Текст з гіперпосиланням на малюнок
                msg_body = f"📅 <b>{date_header}</b> група {current_group}\n⏱ <i>Станом на {new_hours_map[d_str]['site_time']}</i>\n"
                msg_body += f"<a href='{current_imgs[i]}'> Графік відключень.</a>\n\n"
                msg_body += new_hours_map[d_str]["msg"]
                
                r = requests.post(f"{API_URL}/sendMessage", data={
                    'chat_id': CHAT_ID, 'text': msg_body, 'parse_mode': 'HTML'
                }).json()
                mid = r.get('result', {}).get('message_id')
                if mid: new_mids.append(mid)
            save_memory(current_group, new_mids, current_imgs, new_hours_map, current_dates)

        # ДІЯ Б: Змінилося тільки "станом на" та посилання на малюнок -> Редагуємо старі повідомлення
        elif only_time_or_link_changed:
            print("✏️ [Дія] Редагування існуючих повідомлень (оновлення часу/лінку)...")
            for i, d_str in enumerate(current_dates):
                if i >= len(msg_ids): break
                old_time = hours_by_date.get(d_str, {}).get("site_time")
                new_time = new_hours_map[d_str]["site_time"]
                # Підкреслюємо час, якщо він змінився
                time_disp = f"<u>{new_time}</u>" if new_time != old_time else new_time
                
                new_text = f"📅 <b>{d_str}</b> група {current_group}\n⏱ <i>Станом на {time_disp}</i>\n"
                new_text += f"<a href='{current_imgs[i]}'> Графік відключень.</a>\n\n"
                new_text += new_hours_map[d_str]["msg"]
                
                requests.post(f"{API_URL}/editMessageText", data={
                    'chat_id': CHAT_ID, 'message_id': msg_ids[i], 'text': new_text, 'parse_mode': 'HTML'
                })
            save_memory(current_group, msg_ids, current_imgs, new_hours_map, current_dates)

        # ДІЯ В: Якщо графік зник (наприклад, було 2 дні, стало 1) -> Видаляємо зайве
        elif len(msg_ids) > len(current_dates):
            print("🗑 [Дія] Видалення застарілого дня...")
            for _ in range(len(msg_ids) - len(current_dates)):
                mid = msg_ids.pop(0)
                requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
            save_memory(current_group, msg_ids, current_imgs, new_hours_map, current_dates)
        
        else:
            print("✅ [Статус] Змін не виявлено.")

    except Exception as e: 
        print(f"❌ Помилка в циклі: {e}")
    finally:
        if driver: driver.quit()

# --- ЗАПУСК ---
if __name__ == "__main__":
    print(f"🤖 Бот активний. Заплановано циклів: 7. Пауза: 125с.")
    for cycle in range(1):
        print(f"\n--- [Цикл {cycle + 1} з 7] ---")
        check_and_update()
        if cycle < 6:
            time.sleep(1)
    print("\n🏁 Програма завершила 7 циклів. Роботу закінчено.")
