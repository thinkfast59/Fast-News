import os
import re
import json
import hashlib
from io import BytesIO
from datetime import datetime

import numpy as np
import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from gtts import gTTS

from moviepy import (
    VideoClip,
    AudioFileClip,
)


# =========================
# SETTINGS
# =========================

PAGE_NAME = "WORLD PULSE DAILY"

OUTPUT_DIR = "output"
ASSET_DIR = "assets"
USED_FILE = "used.json"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_SIZE = (VIDEO_WIDTH, VIDEO_HEIGHT)

# English = "en"
# Sinhala voice can try = "si"
LANGUAGE = "en"

# Best for reels: 500-700
# Longer video: 1200-2000
MAX_SCRIPT_CHARS = 700

SHOW_SOURCE_TEXT = False

FEEDS = [
    "https://www.bbc.com/news/world/rss.xml",
    "https://feeds.skynews.com/feeds/rss/world.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)


# =========================
# TEXT CLEANING
# =========================

def clean_text(text):
    text = BeautifulSoup(text or "", "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def shorten(text, max_chars):
    text = clean_text(text)

    if len(text) <= max_chars:
        return text

    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut + "..."


# =========================
# USED NEWS MEMORY
# =========================

def load_used():
    if os.path.exists(USED_FILE):
        try:
            with open(USED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    return []


def save_used(used):
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(used[-500:], f, indent=2)


# =========================
# FONT SYSTEM
# =========================

def get_font(size, bold=False):
    if bold:
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "DejaVuSans-Bold.ttf",
        ]
    else:
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "DejaVuSans.ttf",
        ]

    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass

    return ImageFont.load_default()


def text_size(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = current + " " + word if current else word
        width, _ = text_size(draw, test, font)

        if width <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def fit_text_to_box(draw, text, max_width, max_height, start_size, min_size, bold=False):
    for size in range(start_size, min_size - 1, -2):
        font = get_font(size, bold)
        lines = wrap_text(draw, text, font, max_width)

        line_height = int(size * 1.22)
        total_height = len(lines) * line_height

        if total_height <= max_height:
            return font, lines, line_height

    font = get_font(min_size, bold)
    lines = wrap_text(draw, text, font, max_width)
    line_height = int(min_size * 1.22)

    return font, lines, line_height


def draw_multiline(draw, lines, x, y, font, line_height, fill):
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height

    return y


# =========================
# REAL NEWS IMAGE SYSTEM
# =========================

def upgrade_image_url(url):
    if not url:
        return None

    upgraded = url

    # BBC small RSS thumbnail upgrade
    replacements = [
        "/standard/240/",
        "/standard/320/",
        "/standard/480/",
        "/standard/624/",
        "/standard/800/",
        "/ace/standard/240/",
        "/ace/standard/320/",
        "/ace/standard/480/",
        "/ace/standard/624/",
        "/ace/standard/800/",
    ]

    for old in replacements:
        upgraded = upgraded.replace(old, old.replace(old.split("/")[-2], "1024"))

    return upgraded


def get_image_from_feed_entry(entry):
    media_content = entry.get("media_content", [])

    if media_content:
        for media in media_content:
            url = media.get("url")
            if url:
                return upgrade_image_url(url)

    media_thumbnail = entry.get("media_thumbnail", [])

    if media_thumbnail:
        for media in media_thumbnail:
            url = media.get("url")
            if url:
                return upgrade_image_url(url)

    links = entry.get("links", [])

    for link in links:
        href = link.get("href", "")
        media_type = link.get("type", "")

        if href and "image" in media_type:
            return upgrade_image_url(href)

    return None


def get_image_from_article_page(article_url):
    try:
        response = requests.get(
            article_url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        meta_tags = [
            ("meta", {"property": "og:image"}),
            ("meta", {"name": "twitter:image"}),
            ("meta", {"property": "twitter:image"}),
        ]

        for tag_name, attrs in meta_tags:
            tag = soup.find(tag_name, attrs=attrs)

            if tag and tag.get("content"):
                return upgrade_image_url(tag.get("content"))

    except Exception as e:
        print("Article image fetch error:", e)

    return None


def download_image(url, output_path):
    if not url:
        return False

    urls_to_try = []

    upgraded_url = upgrade_image_url(url)

    if upgraded_url:
        urls_to_try.append(upgraded_url)

    if url not in urls_to_try:
        urls_to_try.append(url)

    for try_url in urls_to_try:
        try:
            print("Trying image:", try_url)

            response = requests.get(
                try_url,
                headers={"User-Agent": USER_AGENT},
                timeout=20,
            )

            if response.status_code != 200:
                print("Image status code:", response.status_code)
                continue

            img = Image.open(BytesIO(response.content)).convert("RGB")

            if img.width < 120 or img.height < 120:
                print("Image too small:", img.width, img.height)
                continue

            print("Image downloaded:", img.width, img.height)

            img.save(output_path, quality=95)
            return True

        except Exception as e:
            print("Image download failed:", e)

    return False


def cover_resize(img, size):
    target_w, target_h = size
    img_w, img_h = img.size

    scale = max(target_w / img_w, target_h / img_h)

    new_w = int(img_w * scale)
    new_h = int(img_h * scale)

    img = img.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2

    return img.crop((left, top, left + target_w, top + target_h))


def create_fallback_news_image(path):
    img = Image.new("RGB", VIDEO_SIZE, (8, 16, 35))
    draw = ImageDraw.Draw(img)

    for y in range(VIDEO_HEIGHT):
        ratio = y / VIDEO_HEIGHT

        r = int(8 * (1 - ratio) + 12 * ratio)
        g = int(16 * (1 - ratio) + 55 * ratio)
        b = int(35 * (1 - ratio) + 95 * ratio)

        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=(r, g, b))

    font_big = get_font(90, True)
    font_small = get_font(44, False)

    draw.text((80, 760), "WORLD", font=font_big, fill="white")
    draw.text((80, 870), "NEWS", font=font_big, fill=(255, 60, 60))
    draw.text((80, 1010), "UPDATE", font=font_small, fill="white")

    img.save(path, quality=95)


# =========================
# NEWS COLLECTION
# =========================

def get_news():
    used = load_used()
    news_items = []

    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)

            for entry in feed.entries:
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", ""))
                link = entry.get("link", "")

                if not title or not link:
                    continue

                news_id = hashlib.md5(link.encode("utf-8")).hexdigest()

                if news_id in used:
                    continue

                image_url = get_image_from_feed_entry(entry)

                news_items.append({
                    "id": news_id,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "image_url": image_url,
                    "source": feed.feed.get("title", "News Source"),
                })

        except Exception as e:
            print("Feed error:", feed_url, e)

    if not news_items:
        return None

    news = news_items[0]

    # Try article real image first
    article_image = get_image_from_article_page(news["link"])

    if article_image:
        news["image_url"] = article_image
    elif news["image_url"]:
        news["image_url"] = upgrade_image_url(news["image_url"])

    used.append(news["id"])
    save_used(used)

    return news


# =========================
# VOICE SCRIPT
# =========================

def make_script(news):
    title = shorten(news["title"], 180)
    summary = shorten(news["summary"], MAX_SCRIPT_CHARS)

    if summary:
        script = f"{title}. {summary}."
    else:
        script = f"{title}."

    script += " Follow for more world news updates."

    return script


def create_voice(script, path):
    tts = gTTS(text=script, lang=LANGUAGE, slow=False)
    tts.save(path)


# =========================
# VIDEO DESIGN
# =========================

def add_dark_gradient(img):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for y in range(VIDEO_HEIGHT):
        if y < 620:
            alpha = int(95 + 85 * (1 - y / 620))
        elif y > 1080:
            alpha = int(70 + 160 * ((y - 1080) / 840))
        else:
            alpha = 35

        alpha = max(0, min(230, alpha))

        draw.line(
            [(0, y), (VIDEO_WIDTH, y)],
            fill=(0, 0, 0, alpha)
        )

    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def draw_rounded_panel(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(
        xy,
        radius=radius,
        fill=fill,
        outline=outline,
        width=width,
    )


def create_news_frame(news, image_path, progress=0.0):
    original = Image.open(image_path).convert("RGB")

    # Slow professional zoom animation
    zoom = 1.0 + progress * 0.035

    crop_w = int(original.width / zoom)
    crop_h = int(original.height / zoom)

    left = max(0, (original.width - crop_w) // 2)
    top = max(0, (original.height - crop_h) // 2)

    original = original.crop((left, top, left + crop_w, top + crop_h))

    # Blurred full background
    bg = cover_resize(original, VIDEO_SIZE)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=18))
    bg = add_dark_gradient(bg)

    img = bg.convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Top masthead
    draw.rectangle(
        (0, 0, VIDEO_WIDTH, 175),
        fill=(3, 8, 20, 245)
    )

    mast_font = get_font(58, True)
    draw.text(
        (50, 45),
        PAGE_NAME,
        font=mast_font,
        fill="white"
    )

    date_font = get_font(26, False)
    date_text = datetime.now().strftime("%Y-%m-%d")
    draw.text(
        (820, 78),
        date_text,
        font=date_font,
        fill=(210, 220, 235)
    )

    # Breaking news red bar
    draw_rounded_panel(
        draw,
        (50, 205, 1030, 315),
        28,
        fill=(190, 18, 32, 245),
    )

    breaking_font = get_font(45, True)
    draw.text(
        (92, 234),
        "BREAKING NEWS UPDATE",
        font=breaking_font,
        fill="white"
    )

    draw.ellipse(
        (915, 243, 945, 273),
        fill="white"
    )

    draw.text(
        (958, 235),
        "LIVE",
        font=get_font(34, True),
        fill="white"
    )

    # Sharp main photo card
    photo_x1 = 50
    photo_y1 = 360
    photo_x2 = 1030
    photo_y2 = 1085

    photo_w = photo_x2 - photo_x1
    photo_h = photo_y2 - photo_y1

    photo = cover_resize(original, (photo_w, photo_h))
    photo = photo.filter(ImageFilter.SHARPEN)

    mask = Image.new("L", (photo_w, photo_h), 0)
    mask_draw = ImageDraw.Draw(mask)

    mask_draw.rounded_rectangle(
        (0, 0, photo_w, photo_h),
        radius=38,
        fill=255,
    )

    img.paste(
        photo.convert("RGBA"),
        (photo_x1, photo_y1),
        mask
    )

    draw.rounded_rectangle(
        (photo_x1, photo_y1, photo_x2, photo_y2),
        radius=38,
        outline=(255, 255, 255, 85),
        width=3,
    )

    # Bottom text panel
    panel_top = 1125
    panel_bottom = 1870

    draw_rounded_panel(
        draw,
        (40, panel_top, 1040, panel_bottom),
        38,
        fill=(5, 12, 28, 232),
        outline=(255, 255, 255, 60),
        width=2,
    )

    draw.rounded_rectangle(
        (75, panel_top + 45, 235, panel_top + 60),
        radius=8,
        fill=(235, 30, 45),
    )

    title = shorten(news["title"], 145)
    summary = shorten(news["summary"], 360)

    # Headline auto-fit
    title_font, title_lines, title_lh = fit_text_to_box(
        draw=draw,
        text=title,
        max_width=900,
        max_height=285,
        start_size=58,
        min_size=36,
        bold=True,
    )

    y = panel_top + 90

    y = draw_multiline(
        draw=draw,
        lines=title_lines,
        x=75,
        y=y,
        font=title_font,
        line_height=title_lh,
        fill="white",
    )

    # Summary auto-fit
    summary_y = y + 38

    if summary:
        summary_font, summary_lines, summary_lh = fit_text_to_box(
            draw=draw,
            text=summary,
            max_width=900,
            max_height=310,
            start_size=37,
            min_size=27,
            bold=False,
        )

        draw_multiline(
            draw=draw,
            lines=summary_lines[:7],
            x=75,
            y=summary_y,
            font=summary_font,
            line_height=summary_lh,
            fill=(230, 235, 245),
        )

    if SHOW_SOURCE_TEXT:
        source_font = get_font(25, False)
        source = shorten(news.get("source", ""), 60)

        draw.text(
            (75, 1810),
            f"Source: {source}",
            font=source_font,
            fill=(200, 205, 215),
        )

    return img.convert("RGB")


# =========================
# CREATE VIDEO
# =========================

def create_video(news, image_path, audio_path, output_path):
    audio = AudioFileClip(audio_path)
    duration = audio.duration

    def make_frame(t):
        progress = min(1.0, t / max(duration, 1))

        frame = create_news_frame(
            news=news,
            image_path=image_path,
            progress=progress,
        )

        return np.array(frame)

    video = VideoClip(make_frame, duration=duration)
    video = video.with_audio(audio)

    video.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=2,
    )

    audio.close()
    video.close()


# =========================
# MAIN
# =========================

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(ASSET_DIR, exist_ok=True)

    news = get_news()

    if not news:
        print("No fresh news found.")
        return

    print("Selected news:", news["title"])
    print("Link:", news["link"])
    print("Image:", news["image_url"])

    raw_image_path = os.path.join(ASSET_DIR, "news_image.jpg")
    voice_path = os.path.join(ASSET_DIR, "voice.mp3")
    video_path = os.path.join(OUTPUT_DIR, "auto_video.mp4")

    image_ok = download_image(news["image_url"], raw_image_path)

    if not image_ok:
        print("No real image found. Using fallback background.")
        create_fallback_news_image(raw_image_path)

    script = make_script(news)

    print("Creating voice...")
    create_voice(script, voice_path)

    print("Creating video...")
    create_video(news, raw_image_path, voice_path, video_path)

    print("Video created:", video_path)


if __name__ == "__main__":
    main()
