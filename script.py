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
    """Завантажує історію: час сайту та збережену групу"""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"last_time": "", "group": ""}

def save_memory(last_time, group):
    """Зберігає дані в JSON"""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_time": last_time, "group": group}, f, ensure_ascii=False)

def calculate_duration(start, end):
    """Обчислює різницю часу (напр. 3 г. 30 х.)"""
    try:
        fmt = "%H:%M"
        t1, t2 = datetime.strptime(start, fmt), datetime.strptime(end, fmt)
        diff = t2 - t1
        s = diff.total_seconds()
        return f"{int(s // 3600)} г. {int((s % 3600) // 60)} х."
    except: return ""

def extract_group_info(text_block, group):
    """Шукає статус збереженої групи у блоці тексту"""
    if not group: return ""
    # Шукаємо "Група X.X." (з крапкою)
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, text_block, re.DOTALL)
    if match:
        content = match.group(1).strip()
        if "Електроенергія є." in content:
            return "\n✅ Електроенергія є."
        
        # Шукаємо "Електроенергії немає з 10:30 до 14:00."
        time_match = re.search(r"немає з (\d{2}:\d{2}) до (\d{2}:\d{2})", content)
        if time_match:
            s, e = time_match.groups()
            dur = calculate_duration(s, e)
            return f"\n⚠️ <b>Планове відключення:</b>\n{s} - {e}   ({dur})"
    return ""

def check_and_update():
    memory = load_memory()
    last_site_time = memory.get("last_time", "")
    current_group = memory.get("group", "") # Група тепер завжди підтягується з пам'яті
    
    user_interfered = False
    # Перевірка нових повідомлень (зміна групи або просто запит)
    try:
        res = requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1").json()
        if res.get('result'):
            upd = res['result'][-1]
            msg = upd.get('message', {}).get('text', '')
            update_id = upd['update_id']
            
            # Якщо ввели /2.1 — оновлюємо групу в пам'яті
            cmd = re.search(r"/(\d\.\d)", msg)
            if cmd:
                current_group = cmd.group(1)
                user_interfered = True
            elif msg and 'photo' not in upd.get('message', {}):
                user_interfered = True
                
            # Скидаємо чергу повідомлень
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

        # Умова оновлення: змінився час на сайті АБО була команда від юзера
        if (new_site_time != last_site_time and new_site_time != "") or user_interfered:
            imgs = driver.find_elements(By.XPATH, "//img[contains(@src, '_GPV-mobile.png')]")
            dates = re.findall(r"відключень на (\d{2}\.\d{2}\.\d{4})", full_text)
            # Ділимо текст на блоки по датах
            blocks = re.split(r"Графік погодинних відключень на", full_text)[1:]

            if imgs:
                # Очищення чату (ваша логіка)
                try:
                    r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': '.'}).json()
                    mid = r.get('result', {}).get('message_id')
                    if mid:
                        for i in range(mid, mid - 5, -1):
                            requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", data={'chat_id': CHAT_ID, 'message_id': i})
                except: pass

                for i, img in enumerate(imgs):
                    src = img.get_attribute("src")
                    # Якщо група збережена в пам'яті, шукаємо її статус для кожного графіка
                    group_info = extract_group_info(blocks[i], current_group) if i < len(blocks) else ""
                    
                    header = f"📅 <b>{dates[i]}</b>" if i < len(dates) else "📅"
                    # Якщо група є, додаємо її номер у заголовок для наочності
                    group_label = f" група {current_group}" if current_group else ""
                    
                    cap = f"{header}{group_label}\n⏱ <i>Станом на {found_times[i] if i < len(found_times) else ''}</i>{group_info}"
                    
                    img_data = requests.get(urljoin(URL_SITE, src)).content
                    requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", 
                                 data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML'}, 
                                 files={'photo': ('graph.png', io.BytesIO(img_data))})
                
                save_memory(new_site_time, current_group)
                return True
        else:
            print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Змін немає. Група в пам'яті: {current_group}")
    except Exception as e:
        print(f"❌ Помилка: {e}")
    finally:
        if driver: driver.quit()
    return False

if __name__ == "__main__":
    for cycle in range(5):
        check_and_update()
        if cycle < 4: time.sleep(120)
