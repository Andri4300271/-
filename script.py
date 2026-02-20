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
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "group" not in data or not data["group"]: data["group"] = "1.1"
                if "variant" not in data: data["variant"] = 2
                return data
        except: pass
    return {"group": "1.1", "variant": 2, "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(group, variant, msg_ids, last_imgs, hours_by_date, last_dates):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "group": group, "variant": variant, "msg_ids": msg_ids, 
            "last_imgs": last_imgs, "hours_by_date": hours_by_date, "last_dates": last_dates
        }, f, ensure_ascii=False)

# --- МАТЕМАТИЧНІ ОБЧИСЛЕННЯ ---
def calculate_duration(start, end):
    try:
        fmt = "%H:%M"
        end_proc = "23:59" if end == "24:00" else end
        t1, t2 = datetime.strptime(start, fmt), datetime.strptime(end_proc, fmt)
        diff = t2 - t1
        s = diff.total_seconds()
        if end == "24:00": s += 60
        if s <= 0: return "0 г. 0 х."
        return f"{int(s // 3600)} г. {int((s % 3600) // 60)} х."
    except: return "0 г. 0 х."

# --- ВІЗУАЛІЗАЦІЯ ЗМІН (ПІДКРЕСЛЕННЯ) ---
def format_row(s, e, dur, old_data, is_new_date):
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

# --- ПАРСИНГ ТА РОЗРАХУНОК ---
def extract_group_info(text_block, group, old_data=None):
    if not group: return "", {}
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    current_data = {"periods": [], "light_before": None, "light_after_last": None, "is_full_light": False}
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
            current_data["periods"].append({"start": s, "end": e, "dur": calculate_duration(s, e), "light_after": None})
        if current_data["periods"]:
            was_full_light = old_data.get("is_full_light", False) if old_data else False
            header = "⚠️ <b><u>Планове відключення:</u></b>" if was_full_light and not is_new_date else "⚠️ <b>Планове відключення:</b>"
            res_lines = [header]
            first_p = current_data["periods"]
            l_dur = calculate_duration("00:00", first_p[0]["start"])
            current_data["light_before"] = l_dur
            old_l = old_data.get("light_before") if old_data else None
            l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
            res_lines.append(f"          💡  <i>{l_disp}</i>")
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
            last_e = current_data["periods"][-1]["end"]
            l_dur = calculate_duration(last_e, "24:00")
            current_data["light_after_last"] = l_dur
            old_l = old_data.get("light_after_last") if old_data else None
            l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
            res_lines.append(f"          💡  <i>{l_disp}</i>")
            return "\n".join(res_lines), current_data
    return "", current_data

# --- ОЧИЩЕННЯ ЧАТУ ---
def clear_chat_5(msg_ids):
    print("🧹 [Дія] Початок повної зачистки чату перед оновленням...")
    try:
        r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': '.'}).json()
        last_id = r.get('result', {}).get('message_id')
        if not last_id: return
        if msg_ids:
            start_id = min(msg_ids)
            print(f"🗑 [Процес] Видалення повідомлень від ID {start_id} до ID {last_id} (включно).")
            for mid in range(start_id, last_id + 1):
                requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
        else:
            print("🗑 [Процес] Графіків не знайдено, видаляємо останні 5 повідомлень чату.")
            for i in range(last_id, last_id - 6, -1):
                requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
        time.sleep(1)
        print("✨ [Результат] Чат очищено успішно.")
    except Exception as e: print(f"⚠️ [Помилка] Під час очищення: {e}")

# --- ГОЛОВНА ЛОГІКА ---
def check_and_update():
    print(f"🕒 [{datetime.now().strftime('%H:%M:%S')}] --- ЗАПУСК ПЕРЕВІРКИ ---")
    mem = load_memory()
    current_group, current_variant = mem["group"], mem["variant"]
    msg_ids, last_imgs = mem["msg_ids"], mem["last_imgs"]
    hours_by_date, last_dates = mem["hours_by_date"], mem["last_dates"]
    
    user_interfered = False
    print("📩 [Крок 1] Перевірка нових команд у Telegram-боті...")
    try:
        res = requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1&limit=10").json()
        if res.get('result'):
            for upd in res['result']:
                msg_text = upd.get('message', {}).get('text', '')
                if msg_text:
                    print(f"💬 [Текст] Отримано запит користувача: '{msg_text}'.")
                    user_interfered = True
                    if "/1" in msg_text: current_variant = 1; print("🔄 [Зміна] Обрано ВАРІАНТ 1 (Фото).")
                    if "/2" in msg_text: current_variant = 2; print("🔄 [Зміна] Обрано ВАРІАНТ 2 (Текст).")
                    cmd = re.search(r"(\d\.\d)", msg_text)
                    if cmd:
                        new_group = cmd.group(1)
                        if new_group != current_group:
                            print(f"🎯 [Зміна] Нова група: {new_group}. Очищаємо пам'ять дат.")
                            current_group, hours_by_date, last_dates = new_group, {}, []
                requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={upd['update_id'] + 1}")
        save_memory(current_group, current_variant, msg_ids, last_imgs, hours_by_date, last_dates)
    except Exception as e: print(f"❌ [Помилка] Зв'язок з Telegram API: {e}")

    driver = None
    try:
        print(f"🌐 [Крок 2] Запуск браузера та завантаження {URL_SITE}...")
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=390,1200")
        options.add_argument("user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        time.sleep(10)
        
        full_text = driver.find_element(By.TAG_NAME, "body").text
        current_dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        found_times = re.findall(r"станом на (\d{2}:\d{2})", full_text)
        imgs_elements = driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")
        current_imgs = [img.get_attribute("src") for img in imgs_elements]
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]
        print(f"📊 [Аналіз] На сайті знайдено графіків: {len(current_dates)}.")

        now_obj = datetime.now()
        footer_date = now_obj.strftime("%Y.%m.%d")
        today = now_obj.date()
        site_valid = any(datetime.strptime(d, "%d.%m.%Y").date() >= today for d in current_dates)
        stored_valid = any(datetime.strptime(d, "%d.%m.%Y").date() >= today for d in last_dates)

        # ПЕРЕВІРКА АКТУАЛЬНОСТІ ТА ЗАГЛУШКА
        if not site_valid:
            print("📭 [Результат] Актуальних графіків на сайті немає.")
            no_graph_msg = f"●▬▬▬▬▬▬ஜ۩۞۩ஜ▬▬▬▬▬▬●\n‎░░  <b>Графіків відключень не має.</b> ░░\n●▬▬▬▬▬▬ஜ۩۞۩ஜ▬▬▬▬▬▬●\n                    {footer_date}"
            
            if msg_ids and not stored_valid:
                print("📝 [Дія] Оновлення дати у існуючій заглушці.")
                requests.post(f"https://api.telegram.org{TOKEN}/editMessageText", data={
                    'chat_id': CHAT_ID, 'message_id': msg_ids[0], 'text': no_graph_msg, 'parse_mode': 'HTML'
                })
                save_memory(current_group, current_variant, msg_ids, [], {}, [])
            elif (not stored_valid and last_dates) or user_interfered or not msg_ids:
                print("📢 [Дія] Очищення чату та вивід нової заглушки.")
                clear_chat_5(msg_ids)
                r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={
                    'chat_id': CHAT_ID, 'text': no_graph_msg, 'parse_mode': 'HTML'
                }).json()
                new_mid = r.get('result', {}).get('message_id')
                save_memory(current_group, current_variant, [new_mid] if new_mid else [], [], {}, [])
            return

        new_hours_data_map = {}
        for i, b in enumerate(blocks):
            if i >= len(current_dates): break
            date_str, site_time = current_dates[i], found_times[i] if i < len(found_times) else "00:00"
            txt, dat = extract_group_info(b, current_group, hours_by_date.get(date_str))
            dat["site_time"], dat["full_text_msg"] = site_time, txt
            new_hours_data_map[date_str] = dat

        any_schedule_change = any(d not in hours_by_date or new_hours_data_map[d]["periods"] != hours_by_date[d]["periods"] for d in current_dates)
        any_site_time_change = any(d in hours_by_date and new_hours_data_map[d]["site_time"] != hours_by_date[d].get("site_time") for d in current_dates)
        new_graph_appeared = any(d not in last_dates for d in current_dates)

        should_update = user_interfered or any_schedule_change or new_graph_appeared
        if current_variant == 1 and any_site_time_change: should_update = True

        if should_update:
            print("🚀 [Дія] Помічено зміни! Виконуємо повне оновлення з очищенням.")
            clear_chat_5(msg_ids)
            new_mids = []
            for i, date_str in enumerate(current_dates):
                if i >= len(current_imgs): break
                data = new_hours_data_map[date_str]
                is_new_date = date_str not in last_dates
                date_disp = f"<u>{date_str}</u>" if is_new_date else date_str
                old_st = hours_by_date.get(date_str, {}).get("site_time")
                time_disp = f"<u>{data['site_time']}</u>" if not is_new_date and old_st and data['site_time'] != old_st else data['site_time']
                cap = f"📅 {date_disp} група {current_group}\n⏱ <i>Станом на {time_disp}</i>\n{data['full_text_msg']}"
                
                if current_variant == 1:
                    img_data = requests.get(urljoin(URL_SITE, current_imgs[i])).content
                    r = requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML'}, files={'photo': ('g.png', io.BytesIO(img_data))}).json()
                else:
                    link = f'<b><a href="{urljoin(URL_SITE, current_imgs[i])}">---- Графік відключеннь.</a></b>'
                    r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': f"{link}\n{cap}", 'parse_mode': 'HTML', 'disable_web_page_preview': False}).json()
                mid = r.get('result', {}).get('message_id')
                if mid: new_mids.append(mid)
            save_memory(current_group, current_variant, new_mids, current_imgs, new_hours_data_map, current_dates)
            print("✅ [Результат] Нові повідомлення надіслано.")

        elif current_variant == 2 and any_site_time_change:
            print("📝 [Дія] Тільки зміна часу оновлення. Редагуємо повідомлення (без видалення).")
            for i, d in enumerate(current_dates):
                if i < len(msg_ids):
                    data = new_hours_data_map[d]
                    old_st = hours_by_date.get(d, {}).get("site_time")
                    time_disp = f"<u>{data['site_time']}</u>" if data['site_time'] != old_st else data['site_time']
                    link = f'<b><a href="{urljoin(URL_SITE, current_imgs[i])}">---- Графік відключеннь.</a></b>'
                    new_txt = f"{link}\n📅 {d} група {current_group}\n⏱ <i>Станом на {time_disp}</i>\n{data['full_text_msg']}"
                    requests.post(f"https://api.telegram.org{TOKEN}/editMessageText", data={'chat_id': CHAT_ID, 'message_id': msg_ids[i], 'text': new_txt, 'parse_mode': 'HTML'})
            save_memory(current_group, current_variant, msg_ids, current_imgs, new_hours_data_map, current_dates)
            print("✅ [Результат] Час оновлено успішно.")

        elif len(msg_ids) > len(current_imgs) and site_valid:
            print(f"🗑 [Дія] Один з графіків зник із сайту. Видаляємо застаріле повідомлення.")
            for _ in range(len(msg_ids) - len(current_imgs)):
                mid = msg_ids.pop(0)
                requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
            save_memory(current_group, current_variant, msg_ids, current_imgs, new_hours_data_map, current_dates)
        else: 
            print("✅ [Статус] Дані на сайті ідентичні збереженим. Жодних дій не потрібно.")
            save_memory(current_group, current_variant, msg_ids, last_imgs, hours_by_date, last_dates)
    except Exception as e: print(f"❌ [Помилка] {e}")
    finally:
        if driver: driver.quit(); print("🔌 [Браузер] Сесію завершено.")

if __name__ == "__main__":
    print("🤖 Бот запущено. Починаю роботу...")
    for cycle in range(7):
        print(f"\n--- ЦИКЛ {cycle + 1} З 7 ---")
        check_and_update()
        if cycle < 6:
            print("⏳ [Очікування] 135 секунд до наступної перевірки...")
            time.sleep(135)
    print("\n🏁 [Кінець] Всі цикли виконано.")
