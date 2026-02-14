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

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"group": "3.2", "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(group, msg_ids, last_imgs, hours_by_date, last_dates):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({"group": group, "msg_ids": msg_ids, "last_imgs": last_imgs, 
                   "hours_by_date": hours_by_date, "last_dates": last_dates}, f, ensure_ascii=False, indent=4)

def calculate_duration(start, end):
    try:
        fmt = "%H:%M"; end_proc = "23:59" if end == "24:00" else end
        t1, t2 = datetime.strptime(start, fmt), datetime.strptime(end_proc, fmt)
        diff = t2 - t1; s = diff.total_seconds()
        if end == "24:00": s += 60
        return f"{int(s // 3600)} г. {int((s % 3600) // 60)} х."
    except: return ""

def extract_group_info(text_block, group):
    if not group: return "❌ Група не вказана", {}
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    current_data = {"periods": [], "is_full_light": False}
    if match:
        content = match.group(1).strip()
        if "Електроенергія є." in content and "немає" not in content:
            current_data["is_full_light"] = True
            return "✅ <b>Електроенергія є.</b>", current_data
        all_matches = re.findall(r"(\d{2}:\d{2}) до (\d{2}:\d{2})", content)
        for s, e in all_matches:
            current_data["periods"].append({"start": s, "end": e, "dur": calculate_duration(s, e)})
        if current_data["periods"]:
            res = ["⚠️ <b>Планове відключення:</b>"]; prev = "00:00"
            for p in current_data["periods"]:
                if p["start"] != prev: res.append(f"          💡  <i>{calculate_duration(prev, p['start'])}</i>")
                res.append(f"   <b>{p['start']} - {p['end']}</b>   ({p['dur']})"); prev = p["end"]
            if prev != "24:00": res.append(f"          💡  <i>{calculate_duration(prev, '24:00')}</i>")
            return "\n".join(res), current_data
    return "❌ Дані відсутні", current_data

def check_and_update():
    mem = load_memory()
    current_group = mem.get("group", "3.2")
    msg_ids, last_imgs = mem.get("msg_ids", []), mem.get("last_imgs", [])
    hours_by_date, last_dates = mem.get("hours_by_date", {}), mem.get("last_dates", [])
    
    user_req = False
    try:
        r = requests.get(f"{API_URL}/getUpdates?offset=-1", timeout=5).json()
        if r.get('result'):
            for u in r['result']:
                txt = u.get('message', {}).get('text', '')
                if txt:
                    user_req = True; print(f"📩 [Запит] Текст: '{txt}'")
                    cmd = re.search(r"(\d\.\d)", txt)
                    if cmd: current_group = cmd.group(1); hours_by_date = {}
                requests.get(f"{API_URL}/getUpdates?offset={u['update_id']+1}", timeout=5)
    except: pass

    driver = None
    try:
        opt = Options(); opt.add_argument("--headless=new")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opt)
        driver.get(URL_SITE); print(f"🌐 [Браузер] Перевірка сайту..."); time.sleep(15)
        
        txt_all = driver.find_element(By.TAG_NAME, "body").text
        times = re.findall(r"станом на (\d{2}:\d{2})", txt_all)
        imgs = [i.get_attribute("src") for i in driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")]
        dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", txt_all)
        blocks = re.split(r"Графік погодинних відключень на", txt_all)[1:]

        if not dates: 
            print("🛑 Не знайшов дат на сайті.")
            return

        new_map = {}
        for i, d in enumerate(dates):
            t, dat = extract_group_info(blocks[i] if i < len(blocks) else "", current_group)
            dat.update({"site_time": times[i] if i < len(times) else "00:00", "msg": t})
            new_map[d] = dat

        # Якщо був запит, або змінились дати, або немає повідомлень у пам'яті
        if user_req or dates != last_dates or not msg_ids:
            print(f"🚀 [Надсилання] Починаю відправку {len(dates)} повідомлень...")
            
            # Видаляємо старі повідомлення бота
            for mid in msg_ids:
                try: requests.post(f"{API_URL}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid}, timeout=3)
                except: pass
            
            new_mids = []
            for i, d in enumerate(dates):
                if i >= len(imgs): break
                body = f"📅 <b>{d}</b> група {current_group}\n⏱ <i>Станом на {new_map[d]['site_time']}</i>\n"
                body += f"<a href='{imgs[i]}'>Графік відключень.</a>\n\n{new_map[d]['msg']}"
                
                print(f"📦 [DEBUG] Текст для відправки:\n{body[:100]}...")
                
                try:
                    res = requests.post(f"{API_URL}/sendMessage", 
                                        data={'chat_id': CHAT_ID, 'text': body, 'parse_mode': 'HTML'}, 
                                        timeout=10)
                    resp_json = res.json()
                    print(f"📢 [Відповідь Telegram]: {resp_json}")
                    if resp_json.get('ok'):
                        new_mids.append(resp_json['result']['message_id'])
                except requests.exceptions.RequestException as err:
                    print(f"❌ [ПОМИЛКА МЕРЕЖІ]: {err}")
            
            save_memory(current_group, new_mids, imgs, new_map, dates)
        else:
            print("✅ Без змін.")

    except Exception as e: print(f"💥 Помилка: {e}")
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    for cycle in range(1):
        print(f"\n--- [Цикл {cycle + 1}] ---")
        check_and_update()
        if cycle < 6: time.sleep(1)
