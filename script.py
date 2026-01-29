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

# --- КОНФІГУРАЦІЯ (Береться з GitHub Secrets) ---
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
    """Перевірка нових повідомлень у Telegram"""
    try:
        url = f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1"
        res = requests.get(url).json()
        if res.get('result'):
            last_update = res['result'][-1]
            last_msg = last_update.get('message', {})
            update_id = last_update.get('update_id')
            # Підтверджуємо отримання
            requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={update_id + 1}")
            if 'text' in last_msg and 'photo' not in last_msg:
                return True
    except Exception as e:
        print(f"⚠️ Помилка перевірки чату: {e}")
    return False

def check_and_update():
    last_memory = get_last_memory()
    print(f"📊 Попередня пам'ять: {last_memory}")
    
    driver = None
    try:
        user_interfered = is_last_message_text()
        if user_interfered:
            print("📩 Виявлено нове повідомлення від користувача.")

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        print("🌐 Запуск браузера...")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        
        print("⏳ Очікування завантаження контенту (20 сек)...")
        time.sleep(20) 
        
        all_text = driver.find_element(By.TAG_NAME, "body").text
        time_pattern = r"Інформація станом на (\d{2}:\d{2})"
        site_times = re.findall(time_pattern, all_text)
        current_memory = "|".join(site_times)
        
        print(f"🕒 Час на сайті: {current_memory if current_memory else 'не знайдено'}")

        data_changed = (current_memory != last_memory and current_memory != "")

        if data_changed or user_interfered:
            print("🚀 Починаємо оновлення даних у Telegram...")
            imgs = driver.find_elements(By.XPATH, "//img[contains(@src, 'api.loe.lviv.ua/media/') and contains(@src, '.png')]")
            date_pattern = r"Графік погодинних відключень на (\d{2}\.\d{2}\.\d{4})"
            found_dates = re.findall(date_pattern, all_text)
            
            if imgs:
                for i, img in enumerate(imgs):
                    src = img.get_attribute("src")
                    full_url = urljoin(URL_SITE, src)
                    img_res = requests.get(full_url)
                    
                    if img_res.status_code == 200:
                        header = f"📅 <b>Графік на {found_dates[i]}</b>" if i < len(found_dates) else "📅 <b>Графік</b>"
                        cap = f"{header}\n⏱ <i>Станом на {site_times[i] if i < len(site_times) else '---'}</i>"
                        
                        requests.post(f"https://api.telegram.org{TOKEN}/sendPhoto", 
                                     data={'chat_id': CHAT_ID, 'caption': cap, 'parse_mode': 'HTML'}, 
                                     files={'photo': ('graph.png', io.BytesIO(img_res.content))})
                        print(f"✅ Фото {i+1} надіслано.")
                
                save_memory(current_memory)
                print("💾 Новий стан збережено.")
            else:
                print("🖼 Зображень графіків не знайдено на сторінці.")
        else:
            print("✅ Змін немає, нічого не надсилаємо.")

    except Exception as e:
        print(f"❌ КРИТИЧНА ПОМИЛКА: {e}")
    finally:
        if driver:
            driver.quit()
            print("хост закритий.")

if __name__ == "__main__":
    check_and_update()
