import os, io, re, requests, time
from datetime import datetime
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# --- КОНФІГУРАЦІЯ (з Secrets) ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
URL_SITE = "https://poweron.loe.lviv.ua"
MEMORY_FILE = "last_memory.txt"

def get_last_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def save_memory(data):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        f.write(data)

def is_last_message_text():
    """Перевіряє нові повідомлення в Telegram"""
    try:
        url = f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1"
        res = requests.get(url).json()
        if res.get('result'):
            last_update = res['result'][-1]
            last_msg = last_update.get('message', {})
            update_id = last_update.get('update_id')
            requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={update_id + 1}")
            if 'text' in last_msg and 'photo' not in last_msg:
                return True
    except:
        pass
    return False

def clear_chat_fast():
    """Ваш метод: надсилає крапку і видаляє 5 повідомлень вгору"""
    print("🧹 Очищення чату перед оновленням...")
    try:
        r = requests.post(f"https://api.telegram.org{TOKEN}/sendMessage", 
                         data={'chat_id': CHAT_ID, 'text': '.'}).json()
        last_id = r.get('result', {}).get('message_id')
        if last_id:
            for i in range(last_id, last_id - 5, -1):
                requests.post(f"https://api.telegram.org{TOKEN}/deleteMessage", 
                             data={'chat_id': CHAT_ID, 'message_id': i})
    except:
        pass

def check_and_update():
    last_memory = get_last_memory()
    driver = None
    try:
        user_interfered = is_last_message_text()
        
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        #options.add_argument("user-agent=Mozilla/5.0")
        # 📱 Емуляція мобільного телефону
        # Встановлюємо вузьке вікно (наприклад, 390 пікселів як у iPhone)
        options.add_argument("--window-size=390,1200") 
        # Встановлюємо мобільний User-Agent
        options.add_argument("user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
        
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        time.sleep(15) 
        
        all_text = driver.find_element(By.TAG_NAME, "body").text
        site_times = re.findall(r"Інформація станом на (\d{2}:\d{2})", all_text)
        current_memory = "|".join(site_times)

        if (current_memory != last_memory and current_memory != "") or user_interfered:
            print(f"🚀 Зміни знайдено: {current_memory}")
            imgs = driver.find_elements(By.XPATH, "//img[contains(@src, 'api.loe.lviv.ua/media/') and contains(@src, '.png')]")
            date_pattern = r"Графік погодинних відключень на (\d{2}\.\d{2}\.\d{4})"
            found_dates = re.findall(date_pattern, all_text)
            
            if imgs:
                clear_chat_fast() # Видаляємо старе перед відправкою нового
                for i, img in enumerate(imgs):
                    src = img.get_attribute("src")
                    img_res = requests.get(urljoin(URL_SITE, src))
                    if img_res.status_code == 200:
                        header = f"📅 <b>На {found_dates[i]}</b>" if i < len(found_dates) else "📅"
                        cap = f"{header}\n⏱ <i>Станом на {site_times[i] if i < len(site_times) else ''}</i>"
                        requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", 
                                     data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML'}, 
                                     files={'photo': ('graph.png', io.BytesIO(img_res.content))})
                
                save_memory(current_memory)
                return True
        else:
            print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Змін немає.")
    except Exception as e:
        print(f"❌ Помилка: {e}")
    finally:
        if driver: driver.quit()
    return False

if __name__ == "__main__":
    # Очищуємо вхідну чергу один раз при старті Action
    requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1")
    
    # 5 циклів по 60 секунд
    for cycle in range(5):
        print(f"🌀 Цикл {cycle + 1} з 5...")
        check_and_update()
        if cycle < 4:
            time.sleep(60)
