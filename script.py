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
                if "variant" not in data: data["variant"] = 1
                if not data.get("group"): data["group"] = "1.1"
                return data
        except: pass
    return {"group": "1.1", "variant": 1, "msg_ids": [], "last_imgs": [], "hours_by_date": {}, "last_dates": []}

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
        return f"{int(s // 3600)} г. {int((s % 3600) // 60)} х."
    except: return ""

# --- ВІЗУАЛІЗАЦІЯ ЗМІН ---
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
            first_p = current_data["periods"][0]
            if first_p["start"] != "00:00":
                l_dur = calculate_duration("00:00", first_p["start"])
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
            if last_e != "24:00":
                l_dur = calculate_duration(last_e, "24:00")
                current_data["light_after_last"] = l_dur
                old_l = old_data.get("light_after_last") if old_data else None
                l_disp = f"<u>{l_dur}</u>" if not is_new_date and l_dur != old_l else l_dur
                res_lines.append(f"          💡  <i>{l_disp}</i>")
            return "\n".join(res_lines), current_data
    return "", current_data

# --- ОЧИЩЕННЯ ЧАТУ ---
def clear_chat_5(msg_ids):
    print("🧹 [Дія] Очищення чату...")
    try:
        for mid in msg_ids:
            requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': mid})
        r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': '.'}).json()
        last_id = r.get('result', {}).get('message_id')
        if last_id:
            for i in range(last_id, last_id - 6, -1):
                requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
    except: pass

# --- ГОЛОВНА ЛОГІКА ---
def check_and_update():
    mem = load_memory()
    current_group, current_variant = mem["group"], mem["variant"]
    msg_ids, hours_by_date = mem["msg_ids"], mem["hours_by_date"]
    last_dates, last_imgs = mem["last_dates"], mem["last_imgs"]
    
    user_interfered = False
    try:
        res = requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1&limit=5").json()
        for upd in res.get('result', []):
            txt = upd.get('message', {}).get('text', '')
            if txt:
                user_interfered = True
                if "/1" in txt: current_variant = 1
                if "/2" in txt: current_variant = 2
                g_match = re.search(r"(\d\.\d)", txt)
                if g_match: current_group = g_match.group(1)
            requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={upd['update_id'] + 1}")
    except: pass

    driver = None
    try:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=390,1200")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        time.sleep(7)
        full_text = driver.find_element(By.TAG_NAME, "body").text
        found_times = re.findall(r"станом на (\d{2}:\d{2})", full_text)
        imgs_elements = driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")
        current_imgs = [urljoin(URL_SITE, img.get_attribute("src")) for img in imgs_elements]
        current_dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
        blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

        new_data_map = {}
        for i, b in enumerate(blocks):
            if i >= len(current_dates): break
            date_str, site_time = current_dates[i], found_times[i] if i < len(found_times) else "00:00"
            txt, dat = extract_group_info(b, current_group, hours_by_date.get(date_str))
            dat.update({"site_time": site_time, "full_text_msg": txt, "img": current_imgs[i] if i < len(current_imgs) else ""})
            new_data_map[date_str] = dat

        any_schedule_change = any(d not in hours_by_date or new_data_map[d]["periods"] != hours_by_date[d]["periods"] for d in current_dates)
        any_site_time_change = any(d in hours_by_date and new_data_map[d]["site_time"] != hours_by_date[d].get("site_time") for d in current_dates)
        new_graph_appeared = any(d not in last_dates for d in current_dates)

        # Умова перепублікації
        should_repost = user_interfered or any_schedule_change or new_graph_appeared
        if current_variant == 1 and any_site_time_change: should_repost = True

        if should_repost:
            clear_chat_5(msg_ids)
            new_mids = []
            for d in current_dates:
                data = new_data_map[d]
                cap = f"📅 {d} група {current_group}\n⏱ <i>Станом на {data['site_time']}</i>\n{data['full_text_msg']}"
                if current_variant == 1:
                    img_data = requests.get(data["img"]).content
                    r = requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML'}, files={'photo': ('g.png', io.BytesIO(img_data))}).json()
                else:
                    msg = f'<b><a href="{data["img"]}">Графік відключення.</a></b>\n{cap}'
                    r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': msg, 'parse_mode': 'HTML', 'disable_web_page_preview': False}).json()
                mid = r.get('result', {}).get('message_id')
                if mid: new_mids.append(mid)
            save_memory(current_group, current_variant, new_mids, current_imgs, new_data_map, current_dates)

        elif current_variant == 2 and any_site_time_change:
            for i, d in enumerate(current_dates):
                if i < len(msg_ids):
                    data = new_data_map[d]
                    msg = f'<b><a href="{data["img"]}">Графік відключення.</a></b>\n📅 {d} група {current_group}\n⏱ <i>Станом на {data["site_time"]}</i>\n{data["full_text_msg"]}'
                    requests.post(f"https://api.telegram.org{TOKEN}/editMessageText", data={'chat_id': CHAT_ID, 'message_id': msg_ids[i], 'text': msg, 'parse_mode': 'HTML'})
            save_memory(current_group, current_variant, msg_ids, current_imgs, new_data_map, current_dates)
    except Exception as e: print(f"❌ {e}")
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    for cycle in range(5):
        print(f"\n--- [Цикл {cycle + 1} з 5] ---")
        check_and_update()
        if cycle < 4: time.sleep(120)
