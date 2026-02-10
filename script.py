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
    return {"last_time": "", "group": "", "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

def save_memory(last_time, group, msg_ids, last_imgs, hours_by_date, last_dates):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "last_time": last_time, "group": group, "msg_ids": msg_ids, 
            "last_imgs": last_imgs, "hours_by_date": hours_by_date, "last_dates": last_dates
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

def format_row(s, e, dur, old_rows):
    """Підкреслює ТІЛЬКИ години відключення, якщо вони змінилися."""
    if not old_rows: 
        return f"   <b>{s} - {e}</b>   ({dur})"
    
    s_disp, e_disp = s, e
    # Перевіряємо чи є такий точний період
    exact_match = any(row['start'] == s and row['end'] == e for row in old_rows)
    
    if not exact_match:
        start_exists = any(row['start'] == s for row in old_rows)
        if start_exists:
            e_disp = f"<u>{e}</u>"
        else:
            s_disp, e_disp = f"<u>{s}</u>", f"<u>{e}</u>"
            
    return f"   <b>{s_disp} - {e_disp}</b>   ({dur})"

def extract_group_info(text_block, group, old_rows=None):
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
                    light_dur = calculate_duration(prev_end, s)
                    res_lines.append(f"          💡  <i>{light_dur}</i>")
                
                res_lines.append(format_row(s, e, dur, old_rows))
                prev_end = e
            return "\n".join(res_lines), current_periods
    return "", []

def clear_chat_5(msg_ids):
    try:
        for mid in msg_ids:
            requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
        r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': '.'}).json()
        last_id = r.get('result', {}).get('message_id')
        if last_id:
            for i in range(last_id, last_id - 6, -1):
                requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
    except: pass

def check_and_update():
    mem = load_memory()
    last_site_time, current_group = mem.get("last_time", ""), mem.get("group", "")
    msg_ids, last_imgs = mem.get("msg_ids", []), mem.get("last_imgs", [])
    hours_by_date, last_dates = mem.get("hours_by_date", {}), mem.get("last_dates", [])
    
    user_interfered = False
    try:
        res = requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1").json()
        if res.get('result'):
            upd = res['result'][-1]
            msg = upd.get('message', {}).get('text', '')
            cmd = re.search(r"/(\d\.\d)", msg)
            if cmd:
                new_group = cmd.group(1)
                if new_group != current_group:
                    current_group = new_group
                    hours_by_date = {} # Очищаємо історію розкладу при зміні групи
                user_interfered = True
            elif msg and 'photo' not in upd.get('message', {}):
                user_interfered = True
            requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={upd['update_id'] + 1}")
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
        current_dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

        if (new_site_time != last_site_time and new_site_time != "") or user_interfered:
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
            
            should_full_reset = user_interfered or schedule_changed or new_graph or time_only_changed
            sound_needed = user_interfered or schedule_changed or new_graph

            if should_full_reset:
                #clear_chat_5(msg_ids)
                new_mids = []
                for i in range(len(current_imgs)):
                    cap = f"📅 <b>{current_dates[i]}</b> група {current_group}\n⏱ <i>Станом на {found_times[i] if i<len(found_times) else ''}</i>\n{new_hours_texts[i]}"
                    img_data = requests.get(urljoin(URL_SITE, current_imgs[i])).content
                    r = requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", 
                                     data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML', 'disable_notification': not sound_needed}, 
                                     files={'photo': ('g.png', io.BytesIO(img_data))}).json()
                    mid = r.get('result', {}).get('message_id')
                    if mid: new_mids.append(mid)
                save_memory(new_site_time, current_group, new_mids, current_imgs, new_hours_data_map, current_dates)
                return True
            
            elif len(msg_ids) > len(current_imgs):
                for _ in range(len(msg_ids) - len(current_imgs)):
                    mid = msg_ids.pop(0)
                    requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
                save_memory(new_site_time, current_group, msg_ids, current_imgs, new_hours_data_map, current_dates)

    except Exception as e: print(f"❌ Помилка: {e}")
    finally:
        if driver: driver.quit()
    return False

if __name__ == "__main__":
    for cycle in range(5):
        check_and_update()
        if cycle < 4: time.sleep(120)
