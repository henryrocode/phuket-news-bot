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

# ====================== КОНФИГУРАЦИИ ======================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

WATERMARK_TEXT = "@phuketinsiderth"
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
    # Упрощенная проверка: если новость есть на главной, мы её берем (проверка по хэшу отсеет старые)
    return True 

def fetch_content_data(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        
        title = soup.find("h1").get_text(strip=True) if soup.find("h1") else ""
        
        # Ищем основной блок текста и все картинки в нем
        content_div = soup.find("div", {"id": "the_content"}) or soup.find("div", {"class": "news-content"})
        lead = content_div.get_text(separator=" ", strip=True) if content_div else ""

        valid_images = []
        # Главное фото
        og_img = soup.find("meta", property="og:image")
        if og_img:
            valid_images.append(og_img["content"])
            
        # Дополнительные фото из текста
        if content_div:
            for img in content_div.find_all("img"):
                src = img.get("src")
                if src:
                    if not src.startswith("http"):
                        src = BASE_URL + "/" + src.lstrip("/")
                    if src not in valid_images:
                        valid_images.append(src)
                        
        return title, lead, valid_images, url
    except:
        return None, None, [], url

def generate_post(title, content):
    prompt = f"""
Ты — главный редактор медиа на Пхукете. Сделай пост для Telegram.

ДАННЫЕ:
Title: {title}
Content: {content}

ЗАДАЧА:
1. Переведи заголовок на РУССКИЙ (КАПСОМ) и напиши его своими словами на грамотном русском языке.
2. Напиши краткий пересказ на РУССКОМ (1 абзац).
3. Оставь оригинальный заголовок на АНГЛИЙСКОМ (КАПСОМ).
4. Оставь оригинальный текст на АНГЛИЙСКОМ (кратко).
ПРАВИЛА:
- Между русским и английским блоком — ПУСТАЯ СТРОКА.
- Соблюдай предлоги и падежи: "на Пхукете", "на Патонге" и т.д.
"""
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
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
    print(f"🚀 Старт: {datetime.now()}")
    if not all([GROQ_API_KEY, TELEGRAM_TOKEN, CHAT_ID]):
        return

    posted_hashes = get_hashes()
    r = requests.get(TARGET_SOURCE, headers=HEADERS, timeout=15, verify=False)
    soup = BeautifulSoup(r.text, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        if re.search(r"-\d+\.php$", a["href"]) and "news-phuket" not in a["href"]:
            full_url = BASE_URL + "/" + a["href"].lstrip("/")
            if full_url not in links:
                links.append(full_url)

    found = False
    for url in links[:5]: # Проверяем последние 5 ссылок
        h = hashlib.md5(url.encode()).hexdigest()
        if h in posted_hashes:
            continue

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
                        if len(media) == 0:
                            item.update({"caption": final_caption, "parse_mode": "HTML"})
                        media.append((f"p{i}", p, item))

                if media:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMediaGroup",
                        data={"chat_id": CHAT_ID, "media": json.dumps([m[2] for m in media])},
                        files={m[0]: m[1] for m in media}
                    )
                    save_hash(h)
                    print(f"✅ Готово: {title}")
                    found = True
                    time.sleep(5)
    if not found:
        print("😴 Нового нет.")

if __name__ == "__main__":
    main()
