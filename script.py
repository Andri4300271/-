import os
import io
import re
import requests
import time
from datetime import datetime
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# Данні з Secrets GitHub
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
URL_SITE = "https://poweron.loe.lviv.ua"
MEMORY_FILE = "last_memory.txt"

def get_last_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            return f.read().strip()
    return ""

def save_memory(data):
    with open(MEMORY_FILE, "w") as f:
        f.write(data)

def is_last_message_text():
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

def check_and_update():
    last_memory = get_last_memory()
    driver = None
    try:
        user_interfered = is_last_message_text()
        
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        time.sleep(10) # Даємо сайту більше часу на промальовку
        
        all_text = driver.find_element(By.TAG_NAME, "body").text
        time_pattern = r"Інформація станом на (\d{2}:\d{2})"
        site_times = re.findall(time_pattern, all_text)
        current_memory = "|".join(site_times)

        if (current_memory != last_memory and current_memory != "") or user_interfered:
            imgs = driver.find_elements(By.XPATH, "//img[contains(@src, 'api.loe.lviv.ua/media/') and contains(@src, '.png')]")
            date_pattern = r"Графік погодинних відключень на (\d{2}\.\d{2}\.\d{4})"
            found_dates = re.findall(date_pattern, all_text)
            
            if imgs:
                for i, img in enumerate(imgs):
                    src = img.get_attribute("src")
                    img_res = requests.get(urljoin(URL_SITE, src))
                    if img_res.status_code == 200:
                        header = f"📅 <b>Графік на {found_dates[i]}</b>" if i < len(found_dates) else "📅"
                        update_text = f"Станом на {site_times[i]}" if i < len(site_times) else ""
                        requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", 
                                     data={'chat_id': CHAT_ID, 'caption': f"{header}\n⏱ {update_text}", 'parse_mode': 'HTML'}, 
                                     files={'photo': ('graph.png', io.BytesIO(img_res.content))})
                
                save_memory(current_memory)
                return True # Повертаємо True, якщо були зміни
    except Exception as e:
        print(f"Помилка: {e}")
    finally:
        if driver: driver.quit()
    return False

if __name__ == "__main__":
    check_and_update()
