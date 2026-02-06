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

def load_data():
    """Завантажує пам'ять (час і групу) з одного файлу"""
    default = {"last_time": "", "group": ""}
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return default
    return default

def save_data(last_time, group):
    """Зберігає все в один файл"""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_time": last_time, "group": group}, f, ensure_ascii=False)

def calculate_duration(start, end):
    """Рахує різницю часу"""
    try:
        fmt = "%H:%M"
        tdelta = datetime.strptime(end, fmt) - datetime.strptime(start, fmt)
        hours = int(tdelta.total_seconds() // 3600)
        mins = int((tdelta.total_seconds() % 3600) // 60)
        return f"{hours} год. {mins} хв."
    except: return ""

def extract_status(text_block, group):
    """Шукає дані по конкретній групі"""
    if not group: return ""
    # Шукаємо "Група 2.1." (з крапкою)
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    if match:
        part = match.group(1).strip()
        if "Електроенергія є." in part:
            return "\n✅ Електроенергія є."
        t_match = re.search(r"з (\d{2}:\d{2}) до (\d{2}:\d{2})", part)
        if t_match:
            s, e = t_match.groups()
            return f"\n⚠️ <b>Планове відключення:</b>\n{s} - {e}   ({calculate_duration(s, e)})"
    return ""

def check_and_update():
    data = load_data()
    last_memory = data["last_time"]
    current_group = data["group"]
    
    user_interfered = False
    try:
        # Перевірка нових команд /?.? в Telegram
        res = requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1").json()
        if res.get('result'):
            upd = res['result'][-1]
            txt = upd.get('message', {}).get('text', '')
            requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={upd['update_id'] + 1}")
            
            cmd = re.search(r"/(\d\.\d)", txt)
            if cmd:
                current_group = cmd.group(1)
                user_interfered = True
            elif txt:
                user_interfered = True
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
        
        page_text = driver.find_element(By.TAG_NAME, "body").text
        site_times = re.findall(r"станом на (\d{2}:\d{2})", page_text)
        new_time_str = "|".join(site_times)

        if (new_time_str != last_memory and new_time_str != "") or user_interfered:
            # Розбиваємо текст на блоки по днях
            blocks = re.split(r"Графік погодинних відключень на", page_text)[1:]
            imgs = driver.find_elements(By.XPATH, "//img[contains(@src, 'api.loe.lviv.ua/media/')]")
            dates = re.findall(r"(\d{2}\.\d{2}\.\d{4})", page_text)

            if imgs:
                # Очистка чату (код з вашого оригіналу)
                try:
                    r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': '.'}).json()
                    mid = r.get('result', {}).get('message_id')
                    if mid:
                        for i in range(mid, mid - 5, -1):
                            requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
                except: pass

                for i, img in enumerate(imgs):
                    src = img.get_attribute("src")
                    info = extract_status(blocks[i], current_group) if i < len(blocks) else ""
                    header = f"📅 <b>На {dates[i]}</b>" if i < len(dates) else "📅"
                    cap = f"{header}\n⏱ <i>Станом на {site_times[i] if i < len(site_times) else ''}</i>{info}"
                    
                    img_res = requests.get(urljoin(URL_SITE, src))
                    requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", 
                                 data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML'}, 
                                 files={'photo': ('g.png', io.BytesIO(img_res.content))})
                
                save_data(new_time_str, current_group)
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    for cycle in range(5):
        check_and_update()
        if cycle < 4: time.sleep(120)
