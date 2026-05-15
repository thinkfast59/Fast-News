import os
import re
import json
import random
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
# BASIC SETTINGS
# =========================

PAGE_NAME = "WORLD PULSE DAILY"

OUTPUT_DIR = "output"
ASSET_DIR = "assets"
USED_FILE = "used.json"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_SIZE = (VIDEO_WIDTH, VIDEO_HEIGHT)

# English voice = "en"
# Sinhala voice can try = "si"
LANGUAGE = "en"

# Best for Facebook Reels: 500-700
# Longer videos: 1200-2000
MAX_SCRIPT_CHARS = 700

SHOW_SOURCE_TEXT = False

# Facebook Graph API version
GRAPH_VERSION = "v22.0"

# Around-the-world English RSS sources
FEEDS = [
    # Global / World News
    "https://www.bbc.com/news/world/rss.xml",
    "https://feeds.skynews.com/feeds/rss/world.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://feeds.npr.org/1004/rss.xml",
    "https://www.france24.com/en/rss",
    "https://www.dw.com/en/top-stories/s-9097?maca=en-rss-en-all-1573-rdf",
    "https://www.theguardian.com/world/rss",
    "https://www.cbc.ca/cmlink/rss-world",

    # Asia / South Asia
    "https://www.thehindu.com/news/international/feeder/default.rss",
    "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",
    "https://www.hindustantimes.com/feeds/rss/world-news/rssfeed.xml",
    "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml",
    "https://www.scmp.com/rss/91/feed",

    # Middle East / Africa
    "https://www.middleeasteye.net/rss",
    "https://www.arabnews.com/rss.xml",

    # Business / Tech / Science
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://www.theguardian.com/technology/rss",
    "https://www.theguardian.com/science/rss",
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
        json.dump(used[-1000:], f, indent=2)


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
        size_part = old.split("/")[-2]
        upgraded = upgraded.replace(old, old.replace(size_part, "1024"))

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

    feeds_to_check = FEEDS.copy()
    random.shuffle(feeds_to_check)

    for feed_url in feeds_to_check:
        try:
            print("Checking feed:", feed_url)

            feed = feedparser.parse(feed_url)
            source_name = feed.feed.get("title", "News Source")

            for entry in feed.entries[:10]:
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
                    "source": source_name,
                    "feed_url": feed_url,
                })

        except Exception as e:
            print("Feed error:", feed_url, e)

    if not news_items:
        return None

    random.shuffle(news_items)

    with_image = [item for item in news_items if item.get("image_url")]
    without_image = [item for item in news_items if not item.get("image_url")]

    if with_image:
        news = random.choice(with_image)
    else:
        news = random.choice(without_image)

    article_image = get_image_from_article_page(news["link"])

    if article_image:
        news["image_url"] = article_image
    elif news["image_url"]:
        news["image_url"] = upgrade_image_url(news["image_url"])

    used.append(news["id"])
    save_used(used)

    print("Selected source:", news["source"])

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

    # Breaking news bar
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
# FACEBOOK UPLOAD SYSTEM
# =========================

def get_facebook_settings():
    page_id = os.getenv("1171177946073488")
    page_token = os.getenv("EAAe1MZBQ4ewkBRSBUE83Fh8NZBzqt0FwsFlo2thZCTt2SLiMUgnDVIwBN2iVYpafLfo3OYs9ZBjo3nj4htrWubTYPmlBsKRkcLd3935lJLDsFqOo0IA4GU5CKLKzYQ6FcmeDpO0P1PiRA9vEOczAQACCUBYvNrwOnEBoh7bM9DfG4ETsfX328ZCWioBqSJtDq4IFGEloKqCBCdhz2eIuKTLcT9s7NVHK4MmsozYISNOvFSvVUIqY9bJZBjGvkZD")

    if not page_id or not page_token:
        print("Facebook upload skipped: FB_PAGE_ID or FB_PAGE_TOKEN missing.")
        return None, None

    return page_id, page_token


def make_facebook_caption(news):
    title = shorten(news.get("title", ""), 180)
    summary = shorten(news.get("summary", ""), 350)
    link = news.get("link", "")

    caption = f"{title}\n\n"

    if summary:
        caption += f"{summary}\n\n"

    caption += "Follow World Pulse Daily for more world news updates."

    if link:
        caption += f"\n\nRead more: {link}"

    return caption


def post_facebook_reel(video_path, caption):
    page_id, page_token = get_facebook_settings()

    if not page_id or not page_token:
        return False

    try:
        print("Starting Facebook Reel upload...")

        # Step 1: Start Reel upload session
        start_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/video_reels"

        start_params = {
            "upload_phase": "start",
            "access_token": page_token,
        }

        start_response = requests.post(
            start_url,
            data=start_params,
            timeout=60,
        )

        try:
            start_data = start_response.json()
        except Exception:
            print("Reel start raw response:", start_response.text)
            return False

        print("Reel start response:", start_data)

        if start_response.status_code not in [200, 201]:
            print("Could not start Reel upload.")
            return False

        if "video_id" not in start_data or "upload_url" not in start_data:
            print("Missing video_id or upload_url in Reel start response.")
            return False

        video_id = start_data["video_id"]
        upload_url = start_data["upload_url"]

        file_size = os.path.getsize(video_path)

        # Step 2: Upload video binary
        with open(video_path, "rb") as video_file:
            upload_headers = {
                "Authorization": f"OAuth {page_token}",
                "offset": "0",
                "file_size": str(file_size),
                "Content-Type": "application/octet-stream",
            }

            upload_response = requests.post(
                upload_url,
                headers=upload_headers,
                data=video_file,
                timeout=300,
            )

        print("Reel upload status:", upload_response.status_code)
        print("Reel upload response:", upload_response.text)

        if upload_response.status_code not in [200, 201]:
            print("Reel video upload failed.")
            return False

        # Step 3: Publish Reel
        finish_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/video_reels"

        finish_params = {
            "upload_phase": "finish",
            "video_id": video_id,
            "description": caption,
            "video_state": "PUBLISHED",
            "access_token": page_token,
        }

        finish_response = requests.post(
            finish_url,
            data=finish_params,
            timeout=120,
        )

        try:
            finish_data = finish_response.json()
        except Exception:
            print("Reel finish raw response:", finish_response.text)
            return False

        print("Reel finish response:", finish_data)

        if finish_response.status_code in [200, 201] and not finish_data.get("error"):
            print("Facebook Reel published successfully.")
            return True

        print("Facebook Reel publish failed.")
        return False

    except Exception as e:
        print("Facebook Reel error:", e)
        return False


def post_facebook_video_post(video_path, caption):
    page_id, page_token = get_facebook_settings()

    if not page_id or not page_token:
        return False

    try:
        print("Trying normal Facebook video post...")

        url = f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/videos"

        data = {
            "description": caption,
            "access_token": page_token,
        }

        with open(video_path, "rb") as video_file:
            files = {
                "source": video_file
            }

            response = requests.post(
                url,
                data=data,
                files=files,
                timeout=300,
            )

        try:
            result = response.json()
        except Exception:
            print("Video post raw response:", response.text)
            return False

        print("Video post response:", result)

        if response.status_code in [200, 201] and not result.get("error"):
            print("Facebook video post published successfully.")
            return True

        print("Facebook video post failed.")
        return False

    except Exception as e:
        print("Facebook video post error:", e)
        return False


def upload_to_facebook(video_path, news):
    caption = make_facebook_caption(news)

    # Try Reel first
    reel_ok = post_facebook_reel(video_path, caption)

    if reel_ok:
        return True

    # Fallback: normal video post
    print("Reel failed. Trying normal video post...")
    post_ok = post_facebook_video_post(video_path, caption)

    return post_ok


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
    print("Selected source:", news["source"])
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

    print("Uploading to Facebook...")
    facebook_ok = upload_to_facebook(video_path, news)

    if facebook_ok:
        print("Facebook upload completed.")
    else:
        print("Facebook upload failed or skipped.")


if __name__ == "__main__":
    main()
