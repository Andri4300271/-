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

# --- РОБОТА З ПАМ'ЯТТЮ (Збереження стану бота) ---
def load_memory():
    """Завантажує історію оновлень, ID повідомлень та розклади по датах"""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"last_time": "", "group": "", "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(last_time, group, msg_ids, last_imgs, hours_by_date, last_dates):
    """Зберігає поточний стан у файл для порівняння при наступному запуску"""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "last_time": last_time, "group": group, "msg_ids": msg_ids, 
            "last_imgs": last_imgs, "hours_by_date": hours_by_date, "last_dates": last_dates
        }, f, ensure_ascii=False)

# --- МАТЕМАТИЧНІ ОБЧИСЛЕННЯ (Розрахунок часу) ---
def calculate_duration(start, end):
    """Обчислює різницю між двома точками часу (напр. 06:00 - 11:00 = 5 г. 00 х.)"""
    try:
        fmt = "%H:%M"
        # Корекція для півночі (24:00 -> 23:59 + 1 хв)
        end_proc = "23:59" if end == "24:00" else end
        t1, t2 = datetime.strptime(start, fmt), datetime.strptime(end_proc, fmt)
        diff = t2 - t1
        s = diff.total_seconds()
        if end == "24:00": s += 60
        return f"{int(s // 3600)} г. {int((s % 3600) // 60)} х."
    except: return ""

# --- ВІЗУАЛІЗАЦІЯ ЗМІН (Логіка підкреслення <u>) ---
def format_row(s, e, dur, old_rows):
    """Формує рядок відключення. Підкреслює години, якщо вони змінилися для існуючої дати."""
    if not old_rows: 
        # Якщо дата нова (графік щойно з'явився), нічого не підкреслюємо
        return f"   <b>{s} - {e}</b>   ({dur})"
    
    s_disp, e_disp = s, e
    # Перевіряємо, чи є такий точний період у старій пам'яті
    exact_match = any(row['start'] == s and row['end'] == e for row in old_rows)
    
    if not exact_match:
        # Якщо початок збігається, а кінець інший - підкреслюємо кінець
        if any(row['start'] == s for row in old_rows): 
            e_disp = f"<u>{e}</u>"
        else: 
            # Повністю новий період (вставка) - підкреслюємо обидві точки
            s_disp, e_disp = f"<u>{s}</u>", f"<u>{e}</u>"
            
    return f"   <b>{s_disp} - {e_disp}</b>   ({dur})"

# --- ПАРСИНГ САЙТУ (Витягування даних групи) ---
def extract_group_info(text_block, group, old_rows=None):
    """Знаходить блок тексту для групи та розраховує періоди відключень та світла"""
    if not group: return "", []
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    
    current_periods = []
    if match:
        content = match.group(1).strip()
        if "Електроенергія є." in content and "немає" not in content:
            return "✅ <b>Електроенергія є.</b>", []
        
        all_matches = re.findall(r"(\d{2}:\d{2}) до (\d{2}:\d{2})", content)
        for s, e in all_matches:
            current_periods.append({"start": s, "end": e, "dur": calculate_duration(s, e)})

        if current_periods:
            res_lines = ["⚠️ <b>Планове відключення:</b>"]
            prev_end = None
            for p in current_periods:
                s, e, dur = p['start'], p['end'], p['dur']
                if prev_end:
                    # Розрахунок часу 'зі світлом' між відключеннями
                    light_dur = calculate_duration(prev_end, s)
                    res_lines.append(f"          💡  <i>{light_dur}</i>")
                
                res_lines.append(format_row(s, e, dur, old_rows))
                prev_end = e
            return "\n".join(res_lines), current_periods
    return "", []

# --- ОЧИЩЕННЯ ЧАТУ (Видалення команд та старих графіків) ---
def clear_chat_5(msg_ids):
    """[Пріоритетна подія] Видаляє графіки бота та останні 5 повідомлень у чаті"""
    print("🧹 [Подія] Запуск очищення чату (графіки + 5 останніх повідомлень)...")
    try:
        for mid in msg_ids:
            requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
        # Скидаємо крапку для визначення останнього ID
        r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': '.'}).json()
        last_id = r.get('result', {}).get('message_id')
        if last_id:
            for i in range(last_id, last_id - 6, -1):
                requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
    except Exception as e: print(f"⚠️ [Помилка] Не вдалося очистити чат: {e}")

# --- ГОЛОВНА ЛОГІКА (Перевірка та оновлення) ---
def check_and_update():
    """Основна функція перевірки сайту та взаємодії з Telegram"""
    print(f"🕒 [{datetime.now().strftime('%H:%M:%S')}] Початок циклу перевірки.")
    mem = load_memory()
    last_site_time, current_group = mem.get("last_time", ""), mem.get("group", "")
    msg_ids, last_imgs = mem.get("msg_ids", []), mem.get("last_imgs", [])
    hours_by_date, last_dates = mem.get("hours_by_date", {}), mem.get("last_dates", [])
    
    user_interfered = False
    
    # 📩 ПРІОРИТЕТНА ПОДІЯ: Перевірка Telegram команд до запуску браузера
    print("📩 [Подія] Перевірка вхідних повідомлень у Telegram...")
    try:
        res = requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1").json()
        if res.get('result'):
            upd = res['result'][-1]
            msg = upd.get('message', {}).get('text', '')
            cmd = re.search(r"/(\d\.\d)", msg)
            if cmd:
                new_group = cmd.group(1)
                if new_group != current_group:
                    print(f"🎯 [Подія] Зміна групи на {new_group}. Скидання історії розкладів.")
                    current_group = new_group
                    hours_by_date = {} # Пріоритет: чистий вивід для нової групи
                user_interfered = True
            elif msg and 'photo' not in upd.get('message', {}):
                print(f"💬 [Подія] Отримано текстовий запит: {msg}")
                user_interfered = True
            # Підтвердження отримання повідомлення
            requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={upd['update_id'] + 1}")
    except Exception as e: print(f"❌ [Помилка] Telegram API: {e}")

    # 🌐 ПОДІЯ: Запуск браузера та отримання даних з сайту
    driver = None
    try:
        print("🌐 [Подія] Відкриття сайту poweron.loe.lviv.ua через Selenium...")
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=390,1200")
        options.add_argument("user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        time.sleep(5) # Очікування генерації графіків
        
        full_text = driver.find_element(By.TAG_NAME, "body").text
        found_times = re.findall(r"станом на (\d{2}:\d{2})", full_text)
        new_site_time = "|".join(found_times)
        imgs_elements = driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")
        current_imgs = [img.get_attribute("src") for img in imgs_elements]
        current_dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

        print(f"🔍 [Аналіз] Графіків на сайті: {len(current_imgs)}, Станом на: {new_site_time}")

        # 🚀 ПОДІЯ: Обробка змін та надсилання оновлень
        if (new_site_time != last_site_time and new_site_time != "") or user_interfered:
            print("🚀 [Подія] Виявлено зміни. Аналіз розкладів...")
            new_hours_texts, new_hours_data_map = [], {}
            for i, b in enumerate(blocks):
                date_str = current_dates[i]
                old_d = hours_by_date.get(date_str)
                txt, dat = extract_group_info(b, current_group, old_d)
                new_hours_texts.append(txt)
                new_hours_data_map[date_str] = dat

            # Визначення типу змін
            new_graph = any(d not in last_dates for d in current_dates)
            schedule_changed = any(new_hours_data_map.get(d) != hours_by_date.get(d) for d in current_dates if d in hours_by_date)
            time_only_changed = new_site_time != last_site_time and not schedule_changed and not new_graph
            
            should_full_reset = user_interfered or schedule_changed or new_graph or time_only_changed
            sound_needed = user_interfered or schedule_changed or new_graph

            if should_full_reset:
                ###clear_chat_5(msg_ids)
                new_mids = []
                for i in range(len(current_imgs)):
                    date_str = current_dates[i]
                    # Підкреслення ДАТИ, якщо вона нова
                    date_disp = f"<u>{date_str}</u>" if date_str not in last_dates else date_str
                    
                    # Підкреслення ЧАСУ ОНОВЛЕННЯ, якщо змінився тільки він
                    site_time_val = found_times[i] if i < len(found_times) else ''
                    old_time_val = last_site_time.split('|')[i] if i < len(last_site_time.split('|')) else ''
                    time_disp = f"<u>{site_time_val}</u>" if time_only_changed and site_time_val != old_time_val else site_time_val
                    
                    cap = f"📅 <b>{date_disp}</b> група {current_group}\n⏱ <i>Станом на {time_disp}</i>\n{new_hours_texts[i]}"
                    img_data = requests.get(urljoin(URL_SITE, current_imgs[i])).content
                    
                    # Надсилання нового повідомлення
                    r = requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", 
                                     data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML', 'disable_notification': not sound_needed}, 
                                     files={'photo': ('g.png', io.BytesIO(img_data))}).json()
                    mid = r.get('result', {}).get('message_id')
                    if mid: new_mids.append(mid)
                
                save_memory(new_site_time, current_group, new_mids, current_imgs, new_hours_data_map, current_dates)
                print(f"✅ [Успіх] Графіки оновлено. Звук: {'ТАК' if sound_needed else 'НІ'}")
                return True
            
            # Якщо графік просто зник (минув день)
            elif len(msg_ids) > len(current_imgs):
                print(f"🗑 [Подія] Видалення {len(msg_ids) - len(current_imgs)} застарілого графіка.")
                for _ in range(len(msg_ids) - len(current_imgs)):
                    mid = msg_ids.pop(0)
                    ###requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
                save_memory(new_site_time, current_group, msg_ids, current_imgs, new_hours_data_map, current_dates)
        else: print("✅ [Статус] Змін немає.")

    except Exception as e: print(f"❌ [Критична помилка] {e}")
    finally:
        if driver: 
            print("🔌 [Подія] Закриття браузера.")
            driver.quit()
    return False

# --- ТОЧКА ВХОДУ ---
if __name__ == "__main__":
    for cycle in range(5):
        print(f"\n--- [Цикл {cycle + 1} з 5] ---")
        check_and_update()
        if cycle < 4:
            print("⏳ [Очікування] 120 секунд...")
            time.sleep(120)
