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
# Перевірка: чи токен не порожній
if not TOKEN or not CHAT_ID:
    print("❌ КРИТИЧНО: TOKEN або CHAT_ID не встановлені в системних змінних!")
API_URL = f"https://api.telegram.org{TOKEN}"

# --- РОБОТА З ПАМ'ЯТТЮ ---
def load_memory():
    """Завантаження попереднього стану."""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"group": "3.2", "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(group, msg_ids, last_imgs, hours_by_date, last_dates):
    """Збереження поточного стану."""
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

# --- ПАРСИНГ ---
def extract_group_info(text_block, group, old_data=None):
    """Витягує дані про відключення для конкретної групи."""
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
    """Видаляє старі повідомлення та зачищає останні дії користувача."""
    print(f"🧹 [Очищення] Видаляємо {len(msg_ids)} повідомлень бота...")
    for mid in msg_ids:
        requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
    
    print("🧹 [Очищення] Видалення текстових запитів користувача...")
    # Надсилаємо контрольну крапку
    r_temp = requests.post(f"{API_URL}/sendMessage", data={'chat_id': CHAT_ID, 'text': '...'}).json()
    if r_temp.get('ok'):
        last_id = r_temp['result']['message_id']
        for i in range(last_id, last_id - 10, -1):
            requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})

# --- ГОЛОВНА ЛОГІКА ---
def check_and_update():
    """Основний цикл: Selenium -> Парсинг -> Telegram."""
    mem = load_memory()
    current_group = mem.get("group", "3.2")
    msg_ids = mem.get("msg_ids", [])
    hours_by_date = mem.get("hours_by_date", {})
    last_dates = mem.get("last_dates", [])
    last_imgs = mem.get("last_imgs", [])
    
    # 1. Перевірка команд через getUpdates
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
                    # Пошук групи формату X.X
                    cmd = re.search(r"(\d\.\d)", txt)
                    if cmd: 
                        current_group = cmd.group(1)
                        hours_by_date = {} # Скидаємо при зміні групи
                # Підтверджуємо Telegram, що повідомлення отримано
                requests.get(f"{API_URL}/getUpdates?offset={upd['update_id'] + 1}")
    except Exception as e: print(f"⚠️ Помилка Telegram Updates: {e}")

    # 2. Робота з Selenium
    driver = None
    try:
        options = Options()
        options.add_argument("--headless=new")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        print(f"🌐 [Браузер] Перевірка {URL_SITE} (очікування 15с)...")
        time.sleep(15)
        
        full_text = driver.find_element(By.TAG_NAME, "body").text
        found_times = re.findall(r"станом на (\d{2}:\d{2})", full_text)
        current_imgs = [img.get_attribute("src") for img in driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")]
        current_dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

        if not current_dates:
            print("🛑 [Помилка] Дати на сайті не знайдено.")
            return

        new_hours_map = {}
        for i in range(len(current_dates)):
            d_str = current_dates[i]
            # Безпечно дістаємо блок тексту
            block_content = blocks[i] if i < len(blocks) else ""
            txt, dat = extract_group_info(block_content, current_group, hours_by_date.get(d_str))
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

        # ВИРІШАЛЬНИЙ КРИТЕРІЙ ДЛЯ ПЕРЕНАДСИЛАННЯ
        should_repost = user_req or schedule_changed or new_appeared or not msg_ids or len(msg_ids) != len(current_dates)

        if should_repost:
            clear_chat_all(msg_ids)
            print(f"🚀 [Надсилання] Публікація {len(current_dates)} графіків...")
            new_mids = []
            for i in range(len(current_dates)):
                if i >= len(current_imgs): break
                d_str = current_dates[i]
                
                # Формуємо текст з гіперпосиланням
                body = f"📅 <b>{d_str}</b> група {current_group}\n⏱ <i>Станом на {new_hours_map[d_str]['site_time']}</i>\n"
                body += f"<a href='{current_imgs[i]}'> Графік відключень.</a>\n\n{new_hours_map[d_str]['msg']}"
                
                # ВІДПРАВКА ТА ЛОГУВАННЯ РЕЗУЛЬТАТУ
                r = requests.post(f"{API_URL}/sendMessage", data={
                    'chat_id': CHAT_ID, 
                    'text': body, 
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': False
                }).json()
                
                if r.get('ok'):
                    mid = r['result']['message_id']
                    new_mids.append(mid)
                    print(f"✅ УСПІХ: Повідомлення надіслано! ID: {mid}")
                else:
                    print(f"❌ ПОМИЛКА TELEGRAM: {r.get('description')} (Код: {r.get('error_code')})")
            
            save_memory(current_group, new_mids, current_imgs, new_hours_map, current_dates)

        elif time_or_link_changed:
            print("✏️ [Редагування] Оновлення тексту в існуючих повідомленнях...")
            for i in range(len(current_dates)):
                if i >= len(msg_ids): break
                d_str = current_dates[i]
                body = f"📅 <b>{d_str}</b> група {current_group}\n⏱ <i>Станом на {new_hours_map[d_str]['site_time']}</i>\n"
                body += f"<a href='{current_imgs[i]}'> Графік відключень.</a>\n\n{new_hours_map[d_str]['msg']}"
                
                r_edit = requests.post(f"{API_URL}/editMessageText", data={
                    'chat_id': CHAT_ID, 
                    'message_id': msg_ids[i], 
                    'text': body, 
                    'parse_mode': 'HTML'
                }).json()
                
                if not r_edit.get('ok'):
                    print(f"⚠️ Помилка редагування: {r_edit.get('description')}")
            
            save_memory(current_group, msg_ids, current_imgs, new_hours_map, current_dates)
        else:
            print("✅ [Статус] Дані на сайті ідентичні збереженим. Змін немає.")

    except Exception as e: print(f"💥 Критична помилка виконання: {e}")
    finally:
        if driver: driver.quit()

# --- ЗАПУСК НА 7 ЦИКЛІВ ---
if __name__ == "__main__":
    print(f"🤖 Бот активний (7 циклів). Очікування: 125с.")
    for cycle in range(1):
        print(f"\n--- [Цикл {cycle + 1} з 7] ---")
        check_and_update()
        if cycle < 6:
            time.sleep(1)
    print("\n🏁 Роботу завершено.")
