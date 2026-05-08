import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta
from io import BytesIO

import requests
import urllib3
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ====================== КОНФИГУРАЦИИ (GITHUB SECRETS) ======================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

WATERMARK_TEXT = "@phuketinsiderth"
# В Гитхабе файл хешей лежит в корне репозитория
HASH_FILE = "phuket_news_asia_hashes.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

BASE_URL = "https://www.thephuketnews.com"
TARGET_SOURCE = f"{BASE_URL}/news-phuket.php"

# ====================== ФУНКЦИИ ======================

def get_hashes():
    if not os.path.exists(HASH_FILE):
        return set()
    try:
        with open(HASH_FILE, "r") as f:
            return {line.strip() for line in f.readlines() if line.strip()}
    except:
        return set()

def save_hash(new_hash):
    hashes = get_hashes()
    if new_hash not in hashes:
        hashes.add(new_hash)
        with open(HASH_FILE, "w") as f:
            f.write("\n".join(list(hashes)[-500:]))

def process_image(image_url):
    try:
        if not image_url.startswith("http"):
            image_url = BASE_URL + "/" + image_url.lstrip("/")
        r = requests.get(image_url, timeout=15, verify=False, headers=HEADERS)
        img = Image.open(BytesIO(r.content)).convert("RGB")
        draw = ImageDraw.Draw(img)
        fs = int(img.width * 0.045)
        
        # Поиск шрифта в системе Linux (GitHub)
        font = None
        for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "Arial.ttf"]:
            try:
                font = ImageFont.truetype(path, fs)
                break
            except:
                continue
        if not font:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x, y = img.width - tw - 25, img.height - th - 25
        draw.rectangle([x - 10, y - 8, x + tw + 10, y + th + 8], fill=(0, 0, 0, 160))
        draw.text((x, y), WATERMARK_TEXT, fill=(255, 255, 255), font=font)
        
        bio = BytesIO()
        img.save(bio, "JPEG", quality=85)
        bio.seek(0)
        return bio
    except:
        return None

def is_today(soup):
    try:
        pub_tag = soup.find("meta", property="article:published_time")
        if not pub_tag:
            pub_tag = soup.find("meta", {"http-equiv": "date"})

        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        if pub_tag:
            date_str = pub_tag.get("content", "").split(" ")[0]
            if date_str in [today, yesterday]:
                return True

        page_text = soup.get_text()
        t_fmt = datetime.now().strftime("%-d %b %Y")
        y_fmt = (datetime.now() - timedelta(days=1)).strftime("%-d %b %Y")

        if t_fmt in page_text or y_fmt in page_text:
            return True
        return False
    except:
        return False

def fetch_content_data(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        if not is_today(soup):
            return None, None, [], url
        title = soup.find("h1").get_text(strip=True) if soup.find("h1") else ""
        lead = ""
        meta_desc = soup.find("meta", {"name": "Description"})
        if meta_desc:
            lead = meta_desc.get("content", "").strip()
        valid_images = []
        og_img = soup.find("meta", property="og:image")
        if og_img:
            valid_images.append(og_img["content"])
        return title, lead, valid_images, url
    except:
        return None, None, [], url

def generate_post(title, content):
    prompt = f"""
Напиши новостной пост для Telegram на двух языках (русский и английский).

ДАННЫЕ:
Заголовок: {title}
Текст: {content}

ПРАВИЛА ТЕРМИНАЛОГИИ (ВАЖНО):
1. НИКОГДА не переводи "Motorists" как "Мотористы". Используй: "Водители" или "Автомобилисты".
2. НИКОГДА не пиши "Звучат тревогу". Используй: "Бьют в набат", "Выражают обеспокоенность" или "Встревожены".
3. НИКОГДА не пиши "Хоспиталь". Используй только слово "Госпиталь" или "Больница".
4. Используй правильные предлоги: "на Пхукете", "на Патонге".

ОФОРМЛЕНИЕ:
1. Между русской и английской версиями ДОЛЖНА БЫТЬ ОДНА ПУСТАЯ СТРОКА.
2. Каждая версия начинается с эмодзи и ЗАГОЛОВКА КАПСОМ.
3. Весь текст каждой версии — строго один плотный абзац.
4. В конце поста — блок из 6-8 английских хештегов маленькими буквами.
5. Пиши ТОЛЬКО текст поста, без вступлений.

РЕЗУЛЬТАТ:
"""
    # ... остальной код запроса
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=data,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            timeout=30,
        )
        return r.json()["choices"][0]["message"]["content"].strip()
    except:
        return None

def main():
    print(f"🚀 Запуск проверки: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    if not all([GROQ_API_KEY, TELEGRAM_TOKEN, CHAT_ID]):
        print("❌ Ошибка: Ключи API не найдены в Secrets!")
        return

    try:
        posted_hashes = get_hashes()
        r = requests.get(TARGET_SOURCE, headers=HEADERS, timeout=15, verify=False)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")

        links = [
            BASE_URL + "/" + a["href"].lstrip("/")
            for a in soup.find_all("a", href=True)
            if re.search(r"-\d+\.php$", a["href"])
            and "news-phuket.php" not in a["href"]
        ]

        found_any = False
        for url in list(set(links)):
            h = hashlib.md5(url.encode()).hexdigest()
            if h in posted_hashes:
                continue

            print(f"🔎 Обработка: {url}")
            title, text, images, source_url = fetch_content_data(url)

            if title and images:
                post_body = generate_post(title, text)
                if post_body:
                    final_caption = f"{post_body}\n\n📍 <a href='{source_url}'>Источник / Source</a>"
                    
                    media = []
                    for i, img_url in enumerate(images[:9]):
                        p = process_image(img_url)
                        if p:
                            item = {"type": "photo", "media": f"attach://p{i}"}
                            if i == 0:
                                item.update({"caption": final_caption, "parse_mode": "HTML"})
                            media.append((f"p{i}", p, item))

                    if media:
                        t_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMediaGroup"
                        files = {m[0]: m[1] for m in media}
                        media_json = [m[2] for m in media]
                        resp = requests.post(
                            t_api,
                            data={"chat_id": CHAT_ID, "media": json.dumps(media_json)},
                            files=files,
                        )
                        if resp.status_code == 200:
                            save_hash(h)
                            print(f"✅ Опубликовано: {title}")
                            found_any = True
                            time.sleep(5)

        if not found_any:
            print("😴 Новых новостей нет.")

    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    main()
