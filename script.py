import os, io, re, requests, time
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
GROUP_FILE = "selected_group.txt" # Файл для збереження обраної групи

def get_saved_group():
    if os.path.exists(GROUP_FILE):
        with open(GROUP_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def save_group(group):
    with open(GROUP_FILE, "w", encoding="utf-8") as f:
        f.write(group)

def get_last_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def save_memory(data):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        f.write(data)

def check_for_group_command():
    """Перевіряє чи прийшла команда формату /?.?"""
    try:
        url = f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1"
        res = requests.get(url).json()
        if res.get('result'):
            last_update = res['result'][-1]
            msg_text = last_update.get('message', {}).get('text', '')
            update_id = last_update.get('update_id')
            # Підтверджуємо отримання
            requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset={update_id + 1}")
            
            match = re.search(r"/(\d\.\d)", msg_text)
            if match:
                group = match.group(1)
                save_group(group)
                return group, True
            return None, True # Повертаємо True як ознаку активності, навіть якщо не група
    except:
        pass
    return None, False

def calculate_duration(start_str, end_str):
    """Рахує різницю в часі"""
    fmt = "%H:%M"
    tdelta = datetime.strptime(end_str, fmt) - datetime.strptime(start_str, fmt)
    seconds = tdelta.total_seconds()
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours} год. {minutes} хв."

def extract_status_for_group(all_text, group):
    """Шукає статус для конкретної групи в тексті"""
    if not group: return ""
    
    # Шукаємо блок тексту після "Група X.X"
    pattern = rf"Група {group}\.(.*?)(?=Група \d\.\d|$)"
    match = re.search(pattern, all_text, re.DOTALL)
    
    if match:
        status_text = match.group(1).strip()
        if "Електроенергія є" in status_text:
            return "\n✅ Електроенергія є."
        
        # Шукаємо час відключення
        time_match = re.search(r"Електроенергії немає з (\d{2}:\d{2}) до (\d{2}:\d{2})", status_text)
        if time_match:
            start, end = time_match.groups()
            duration = calculate_duration(start, end)
            return f"\n⚠️ <b>Планове відключення:</b>\n{start} - {end}   ({duration})"
    return ""

def clear_chat_fast():
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
    new_group, user_interfered = check_for_group_command()
    current_group = get_saved_group()
    
    driver = None
    try:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=390,1200") 
        options.add_argument("user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
        
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(URL_SITE)
        time.sleep(15) 
        
        all_text = driver.find_element(By.TAG_NAME, "body").text
        site_times = re.findall(r"Інформація станом на (\d{2}:\d{2})", all_text)
        current_memory = "|".join(site_times)

        # Оновлюємо якщо змінився час на сайті АБО якщо користувач надіслав повідомлення
        if (current_memory != last_memory and current_memory != "") or user_interfered:
            print(f"🚀 Оновлення даних (Група: {current_group})")
            imgs = driver.find_elements(By.XPATH, "//img[contains(@src, 'api.loe.lviv.ua/media/') and contains(@src, '.png')]")
            found_dates = re.findall(r"Графік погодинних відключень на (\d{2}\.\d{2}\.\d{4})", all_text)
            
            # Розділяємо текст на блоки по датах для точного пошуку статусу групи
            date_blocks = re.split(r"Графік погодинних відключень на \d{2}\.\d{2}\.\d{4}", all_text)[1:]

            if imgs:
                clear_chat_fast()
                for i, img in enumerate(imgs):
                    src = img.get_attribute("src")
                    img_res = requests.get(urljoin(URL_SITE, src))
                    if img_res.status_code == 200:
                        header = f"📅 <b>На {found_dates[i]}</b>" if i < len(found_dates) else "📅"
                        
                        # Додаємо статус групи під графік
                        group_info = ""
                        if current_group and i < len(date_blocks):
                            group_info = extract_status_for_group(date_blocks[i], current_group)
                        
                        cap = f"{header}\n⏱ <i>Станом на {site_times[i] if i < len(site_times) else ''}</i>{group_info}"
                        
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
    requests.get(f"https://api.telegram.org{TOKEN}/getUpdates?offset=-1")
    for cycle in range(5):
        print(f"🌀 Цикл {cycle + 1} з 1...")
        check_and_update()
        if cycle < 0:
            time.sleep(120)
