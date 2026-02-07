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

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"last_time": "", "group": "", "msg_ids": [], "last_imgs": [], "last_hours": []}

def save_memory(last_time, group, msg_ids, last_imgs, last_hours):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "last_time": last_time, "group": group, 
            "msg_ids": msg_ids, "last_imgs": last_imgs, "last_hours": last_hours
        }, f, ensure_ascii=False)

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

def extract_group_info(text_block, group):
    if not group: return ""
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    if match:
        content = match.group(1).strip()
        if "Електроенергія є." in content and "немає" not in content:
            return "✅ Електроенергія є."
        all_periods = re.findall(r"(\d{2}:\d{2}) до (\d{2}:\d{2})", content)
        if all_periods:
            res_lines = ["⚠️ <b>Планове відключення:</b>"]
            for s, e in all_periods:
                res_lines.append(f"{s} - {e}   ({calculate_duration(s, e)})")
            return "\n".join(res_lines)
    return ""

def check_and_update():
    mem = load_memory()
    last_site_time = mem.get("last_time", "")
    current_group = mem.get("group", "")
    msg_ids = mem.get("msg_ids", [])
    last_imgs = mem.get("last_imgs", [])
    last_hours = mem.get("last_hours", [])
    
    user_interfered = False
    try:
        res = requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1").json()
        if res.get('result'):
            upd = res['result'][-1]
            msg = upd.get('message', {}).get('text', '')
            update_id = upd['update_id']
            cmd = re.search(r"/(\d\.\d)", msg)
            if cmd:
                current_group = cmd.group(1)
                user_interfered = True
            elif msg: user_interfered = True
            requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={update_id + 1}")
    except: pass

    driver = None
    try:
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
        dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

        if (new_site_time != last_site_time and new_site_time != "") or user_interfered:
            current_hours = [extract_group_info(b, current_group) for b in blocks]
            new_msg_ids = []
            
            # Якщо графіків стало менше — видаляємо зайві повідомлення
            if len(msg_ids) > len(current_imgs):
                for j in range(len(current_imgs), len(msg_ids)):
                    requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': msg_ids[j]})
                msg_ids = msg_ids[:len(current_imgs)]

            for i in range(len(current_imgs)):
                info = current_hours[i] if i < len(current_hours) else ""
                header = f"📅 <b>{dates[i]}</b>" if i < len(dates) else "📅"
                cap = f"{header} група {current_group}\n⏱ <i>Станом на {found_times[i] if i < len(found_times) else ''}</i>\n{info}"
                
                is_new_day = i >= len(msg_ids)
                hours_changed = not is_new_day and (current_hours[i] != last_hours[i])
                img_changed = not is_new_day and (current_imgs[i] != last_imgs[i])

                # Логіка звуку: звук, якщо новий день АБО змінився розклад годин
                silent = not (is_new_day or hours_changed)

                if is_new_day or img_changed or hours_changed or user_interfered:
                    # Видаляємо старе перед надсиланням нового (якщо це заміна)
                    if not is_new_day:
                        requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': msg_ids[i]})
                    
                    img_data = requests.get(urljoin(URL_SITE, current_imgs[i])).content
                    r = requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", 
                                     data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML', 'disable_notification': silent}, 
                                     files={'photo': ('graph.png', io.BytesIO(img_data))}).json()
                    
                    mid = r.get('result', {}).get('message_id')
                    if is_new_day: new_msg_ids.append(mid)
                    else: msg_ids[i] = mid
                else:
                    # Тільки оновлення тексту (тихо)
                    requests.post(f"https://api.telegram.org{TOKEN}/editMessageCaption", 
                                 data={'chat_id': CHAT_ID, 'message_id': msg_ids[i], 'caption': cap, 'parse_mode': 'HTML'})
            
            final_msg_ids = msg_ids + new_msg_ids
            save_memory(new_site_time, current_group, final_msg_ids, current_imgs, current_hours)
            return True
    except Exception as e:
        print(f"❌ Помилка: {e}")
    finally:
        if driver: driver.quit()
    return False

if __name__ == "__main__":
    for cycle in range(5):
        check_and_update()
        if cycle < 4: time.sleep(120)
