import os
import re
import json
import time
import random
import hashlib
from io import BytesIO
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import numpy as np
import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from gtts import gTTS

try:
    from moviepy import VideoClip, AudioFileClip
except Exception:
    from moviepy.editor import VideoClip, AudioFileClip

# =========================================================
# WORLD PULSE DAILY - RANDOM US NEWS VIDEO BOT
# Runs once by default. Use GitHub Actions cron for offline scheduling.
# For VPS/local always-on mode: set RUN_FOREVER=1.
# =========================================================

# =========================
# SETTINGS
# =========================
PAGE_NAME = os.getenv("PAGE_NAME", "WORLD PULSE DAILY")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
ASSET_DIR = os.getenv("ASSET_DIR", "assets")
STATE_DIR = os.getenv("STATE_DIR", "state")
USED_FILE = os.path.join(STATE_DIR, "used.json")
LATEST_FILE = os.path.join(OUTPUT_DIR, "latest_news.json")

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_SIZE = (VIDEO_WIDTH, VIDEO_HEIGHT)

LANGUAGE = os.getenv("LANGUAGE", "en")
MAX_SCRIPT_CHARS = int(os.getenv("MAX_SCRIPT_CHARS", "950"))
MIN_IMAGE_SIZE = int(os.getenv("MIN_IMAGE_SIZE", "240"))

# Scheduler options
RUN_FOREVER = os.getenv("RUN_FOREVER", "0") == "1"
SCHEDULE_EVERY_MINUTES = int(os.getenv("SCHEDULE_EVERY_MINUTES", "180"))

# News selection options
# 0.75 means about 75% of selections prefer US feeds, 25% can be world/tech/business.
US_NEWS_RATIO = float(os.getenv("US_NEWS_RATIO", "0.75"))
MAX_ENTRIES_PER_FEED = int(os.getenv("MAX_ENTRIES_PER_FEED", "15"))
MAX_NEWS_AGE_HOURS = int(os.getenv("MAX_NEWS_AGE_HOURS", "72"))

# 1 = hide/blur likely logo corners inside the news image.
HIDE_IMAGE_CORNER_LOGOS = os.getenv("HIDE_IMAGE_CORNER_LOGOS", "1") == "1"
SHOW_SOURCE_TEXT = os.getenv("SHOW_SOURCE_TEXT", "0") == "1"
POST_TO_FACEBOOK = os.getenv("POST_TO_FACEBOOK", "1") == "1"

FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
FB_GRAPH_VERSION = os.getenv("FB_GRAPH_VERSION", "v25.0")

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 WorldPulseDailyBot/2.0",
)

# Mostly US news feeds
US_FEEDS = [
    "https://feeds.npr.org/1003/rss.xml",  # NPR News
    "https://feeds.npr.org/1001/rss.xml",  # NPR Top Stories
    "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    "https://feeds.washingtonpost.com/rss/national",
    "https://feeds.washingtonpost.com/rss/politics",
    "https://www.cbsnews.com/latest/rss/us",
    "https://www.cbsnews.com/latest/rss/politics",
    "https://www.cbsnews.com/latest/rss/world",
    "https://abcnews.go.com/abcnews/usheadlines",
    "https://abcnews.go.com/abcnews/politicsheadlines",
    "https://abcnews.go.com/abcnews/topstories",
    "https://feeds.nbcnews.com/nbcnews/public/news",
    "https://feeds.nbcnews.com/nbcnews/public/politics",
    "https://feeds.nbcnews.com/nbcnews/public/us-news",
    "https://www.latimes.com/world-nation/rss2.0.xml",
    "https://www.latimes.com/california/rss2.0.xml",
    "https://www.usatoday.com/rss/news/",
    "https://www.usatoday.com/rss/news/nation/",
    "https://www.usnews.com/rss/news",
    "https://www.usnews.com/rss/news/national-news",
]

# Backup mixed/world feeds
WORLD_FEEDS = [
    "https://feeds.skynews.com/feeds/rss/world.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://feeds.npr.org/1004/rss.xml",
    "https://www.france24.com/en/rss",
    "https://www.dw.com/en/top-stories/s-9097?maca=en-rss-en-all-1573-rdf",
    "https://www.theguardian.com/world/rss",
    "https://www.cbc.ca/cmlink/rss-world",
    "https://www.bbc.com/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://www.theguardian.com/technology/rss",
    "https://www.theguardian.com/science/rss",
]

FEEDS = US_FEEDS + WORLD_FEEDS

# =========================
# TEXT CLEANING
# =========================
def clean_text(text: str) -> str:
    text = BeautifulSoup(text or "", "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\bRead more\b.*$", "", text, flags=re.I).strip()
    return text


def shorten(text: str, max_chars: int) -> str:
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "..."


def safe_filename(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", text).strip("_")
    return text[:80] or "news"

# =========================
# USED NEWS MEMORY
# =========================
def load_used() -> list:
    if os.path.exists(USED_FILE):
        try:
            with open(USED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as e:
            print("Used-file read error:", e)
    return []


def save_used(used: list) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(used[-2500:], f, indent=2, ensure_ascii=False)

# =========================
# FONT SYSTEM
# =========================
def get_font(size: int, bold: bool = False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for path in paths:
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
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
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
        if len(lines) * line_height <= max_height:
            return font, lines, line_height
    font = get_font(min_size, bold)
    return font, wrap_text(draw, text, font, max_width), int(min_size * 1.22)


def draw_multiline(draw, lines, x, y, font, line_height, fill):
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y

# =========================
# IMAGE SYSTEM
# =========================
def upgrade_image_url(url):
    if not url:
        return None
    upgraded = url
    for old in [
        "/standard/240/", "/standard/320/", "/standard/480/", "/standard/624/", "/standard/800/",
        "/ace/standard/240/", "/ace/standard/320/", "/ace/standard/480/", "/ace/standard/624/", "/ace/standard/800/",
    ]:
        size_part = old.split("/")[-2]
        upgraded = upgraded.replace(old, old.replace(size_part, "1024"))
    return upgraded


def get_image_from_feed_entry(entry):
    for key in ["media_content", "media_thumbnail"]:
        for media in entry.get(key, []) or []:
            url = media.get("url")
            if url:
                return upgrade_image_url(url)

    for link in entry.get("links", []) or []:
        href = link.get("href", "")
        media_type = link.get("type", "")
        if href and "image" in media_type:
            return upgrade_image_url(href)

    enclosure = entry.get("enclosures", [])
    for item in enclosure:
        href = item.get("href", "")
        media_type = item.get("type", "")
        if href and "image" in media_type:
            return upgrade_image_url(href)

    return None


def get_image_from_article_page(article_url):
    try:
        r = requests.get(article_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for tag_name, attrs in [
            ("meta", {"property": "og:image"}),
            ("meta", {"name": "twitter:image"}),
            ("meta", {"property": "twitter:image"}),
        ]:
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
    upgraded = upgrade_image_url(url)
    if upgraded:
        urls_to_try.append(upgraded)
    if url not in urls_to_try:
        urls_to_try.append(url)

    for try_url in urls_to_try:
        try:
            print("Trying image:", try_url)
            r = requests.get(try_url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if r.status_code != 200:
                print("Image status code:", r.status_code)
                continue
            img = Image.open(BytesIO(r.content)).convert("RGB")
            if img.width < MIN_IMAGE_SIZE or img.height < MIN_IMAGE_SIZE:
                print("Image too small:", img.width, img.height)
                continue
            img.save(output_path, quality=95)
            print("Image downloaded:", img.width, img.height)
            return True
        except Exception as e:
            print("Image download failed:", e)
    return False


def cover_resize(img, size):
    target_w, target_h = size
    img_w, img_h = img.size
    scale = max(target_w / img_w, target_h / img_h)
    new_w, new_h = int(img_w * scale), int(img_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left, top = (new_w - target_w) // 2, (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def blur_corner_logos(img):
    if not HIDE_IMAGE_CORNER_LOGOS:
        return img
    img = img.convert("RGB")
    w, h = img.size
    boxes = [
        (0, 0, int(w * 0.24), int(h * 0.15)),
        (int(w * 0.76), 0, w, int(h * 0.15)),
        (0, int(h * 0.85), int(w * 0.24), h),
        (int(w * 0.76), int(h * 0.85), w, h),
    ]
    for box in boxes:
        crop = img.crop(box).filter(ImageFilter.GaussianBlur(radius=18))
        img.paste(crop, box)
    return img


def create_fallback_news_image(path):
    img = Image.new("RGB", VIDEO_SIZE, (8, 16, 35))
    draw = ImageDraw.Draw(img)
    for y in range(VIDEO_HEIGHT):
        ratio = y / VIDEO_HEIGHT
        fill = (int(8 + 4 * ratio), int(16 + 39 * ratio), int(35 + 60 * ratio))
        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=fill)
    draw.text((80, 760), "WORLD", font=get_font(90, True), fill="white")
    draw.text((80, 870), "PULSE", font=get_font(90, True), fill=(255, 60, 60))
    draw.text((80, 1010), "DAILY UPDATE", font=get_font(44), fill="white")
    img.save(path, quality=95)

# =========================
# NEWS COLLECTION
# =========================
def parse_entry_time(entry):
    raw = entry.get("published") or entry.get("updated") or ""
    if raw:
        try:
            return parsedate_to_datetime(raw).astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def is_recent(published_at_iso: str) -> bool:
    try:
        published_at = datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - published_at).total_seconds()
        return age_seconds <= MAX_NEWS_AGE_HOURS * 3600
    except Exception:
        return True


def collect_candidates(feed_urls, feed_group):
    used = set(load_used())
    candidates = []
    shuffled_feeds = list(feed_urls)
    random.shuffle(shuffled_feeds)

    for feed_url in shuffled_feeds:
        try:
            print("Checking feed:", feed_url)
            feed = feedparser.parse(feed_url)
            source_name = clean_text(feed.feed.get("title", "News Source"))
            entries = list(feed.entries[:MAX_ENTRIES_PER_FEED])
            random.shuffle(entries)

            for entry in entries:
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
                link = entry.get("link", "")

                if not title or not link:
                    continue

                news_id = hashlib.sha256(link.encode("utf-8")).hexdigest()
                if news_id in used:
                    continue

                published_at = parse_entry_time(entry).isoformat()
                if not is_recent(published_at):
                    continue

                candidates.append({
                    "id": news_id,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "image_url": get_image_from_feed_entry(entry),
                    "source": source_name,
                    "feed_group": feed_group,
                    "feed_url": feed_url,
                    "published_at": published_at,
                })
        except Exception as e:
            print("Feed error:", feed_url, e)

    return candidates


def choose_random_news(candidates):
    if not candidates:
        return None

    # Prefer candidates with images, but still random.
    image_candidates = [c for c in candidates if c.get("image_url")]
    pool = image_candidates if image_candidates and random.random() < 0.80 else candidates

    # Latest 50 only to avoid old random stories.
    pool.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    pool = pool[:50]
    random.shuffle(pool)
    return pool[0]


def get_news():
    prefer_us = random.random() < US_NEWS_RATIO
    first_feeds = US_FEEDS if prefer_us else FEEDS
    first_group = "US" if prefer_us else "MIXED"

    candidates = collect_candidates(first_feeds, first_group)

    # If US feeds are empty or already used, fallback to all feeds.
    if not candidates and first_feeds != FEEDS:
        print("No fresh US news found. Trying all feeds.")
        candidates = collect_candidates(FEEDS, "MIXED")

    news = choose_random_news(candidates)
    if not news:
        return None

    article_image = get_image_from_article_page(news["link"])
    if article_image:
        news["image_url"] = article_image
    elif news.get("image_url"):
        news["image_url"] = upgrade_image_url(news["image_url"])

    print("Selected source:", news["source"])
    print("Selected group:", news.get("feed_group"))
    return news

# =========================
# VOICE SCRIPT
# =========================
def make_script(news):
    title = shorten(news["title"], 180)
    summary = shorten(news.get("summary", ""), MAX_SCRIPT_CHARS)

    openings = [
        "Here is a quick news update.",
        "This is the latest update.",
        "Here is what is happening now.",
        "Here is today’s important update.",
        "This story is developing now.",
    ]
    closing = [
        "Follow World Pulse Daily for more updates.",
        "Stay with World Pulse Daily for more news.",
        "For more updates, follow World Pulse Daily.",
        "World Pulse Daily will keep you updated.",
    ]

    if summary:
        script = f"{random.choice(openings)} {title}. {summary}. {random.choice(closing)}"
    else:
        script = f"{random.choice(openings)} {title}. More details are still developing. {random.choice(closing)}"

    return clean_text(script)


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
        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=(0, 0, 0, max(0, min(230, alpha))))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def draw_rounded_panel(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def create_news_frame(news, image_path, progress=0.0):
    original = Image.open(image_path).convert("RGB")
    zoom = 1.0 + progress * 0.035
    crop_w, crop_h = int(original.width / zoom), int(original.height / zoom)
    left, top = max(0, (original.width - crop_w) // 2), max(0, (original.height - crop_h) // 2)
    original = original.crop((left, top, left + crop_w, top + crop_h))

    bg = cover_resize(original, VIDEO_SIZE).filter(ImageFilter.GaussianBlur(radius=18))
    bg = add_dark_gradient(bg)
    img = bg.convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Own branding only.
    draw.rectangle((0, 0, VIDEO_WIDTH, 175), fill=(3, 8, 20, 245))
    draw.text((50, 45), PAGE_NAME, font=get_font(58, True), fill="white")
    draw.text((815, 78), datetime.now().strftime("%Y-%m-%d"), font=get_font(26), fill=(210, 220, 235))

    labels = ["NEWS UPDATE", "US NEWS UPDATE", "LATEST UPDATE", "BREAKING UPDATE"]
    draw_rounded_panel(draw, (50, 205, 1030, 315), 28, fill=(190, 18, 32, 245))
    draw.text((92, 234), random.choice(labels), font=get_font(45, True), fill="white")
    draw.ellipse((915, 243, 945, 273), fill="white")
    draw.text((958, 235), "LIVE", font=get_font(34, True), fill="white")

    photo_box = (50, 360, 1030, 1085)
    photo_w, photo_h = photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]
    photo = cover_resize(original, (photo_w, photo_h)).filter(ImageFilter.SHARPEN)
    photo = blur_corner_logos(photo)
    mask = Image.new("L", (photo_w, photo_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, photo_w, photo_h), radius=38, fill=255)
    img.paste(photo.convert("RGBA"), (photo_box[0], photo_box[1]), mask)
    draw.rounded_rectangle(photo_box, radius=38, outline=(255, 255, 255, 85), width=3)

    panel_top, panel_bottom = 1125, 1870
    draw_rounded_panel(draw, (40, panel_top, 1040, panel_bottom), 38, fill=(5, 12, 28, 232), outline=(255, 255, 255, 60), width=2)
    draw.rounded_rectangle((75, panel_top + 45, 235, panel_top + 60), radius=8, fill=(235, 30, 45))

    title = shorten(news["title"], 145)
    summary = shorten(news.get("summary", ""), 360)
    title_font, title_lines, title_lh = fit_text_to_box(draw, title, 900, 285, 58, 36, True)
    y = draw_multiline(draw, title_lines, 75, panel_top + 90, title_font, title_lh, "white")

    if summary:
        summary_font, summary_lines, summary_lh = fit_text_to_box(draw, summary, 900, 310, 37, 27, False)
        draw_multiline(draw, summary_lines[:7], 75, y + 38, summary_font, summary_lh, (230, 235, 245))

    if SHOW_SOURCE_TEXT:
        draw.text((75, 1810), f"Source: {shorten(news.get('source', ''), 60)}", font=get_font(25), fill=(200, 205, 215))

    return img.convert("RGB")

# =========================
# CREATE VIDEO + FACEBOOK POST
# =========================
def create_video(news, image_path, audio_path, output_path):
    audio = AudioFileClip(audio_path)
    duration = min(max(float(audio.duration), 8), 90)

    def make_frame(t):
        progress = min(1.0, t / max(duration, 1))
        return np.array(create_news_frame(news, image_path, progress=progress))

    video = VideoClip(make_frame, duration=duration)

    # MoviePy v2 uses with_audio; MoviePy v1 uses set_audio.
    if hasattr(video, "with_audio"):
        video = video.with_audio(audio)
    else:
        video = video.set_audio(audio)

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


def facebook_caption(news):
    title = shorten(news["title"], 220)
    caption = (
        f"{title}\n\n"
        "Watch the latest update from World Pulse Daily.\n\n"
        "#WorldPulseDaily #USNews #WorldNews #BreakingNews #NewsUpdate"
    )
    return caption


def post_video_to_facebook(video_path, caption):
    if not POST_TO_FACEBOOK:
        print("Facebook posting disabled by POST_TO_FACEBOOK=0")
        return None
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        print("Facebook posting skipped: missing FB_PAGE_ID or FB_PAGE_ACCESS_TOKEN secrets.")
        return None

    url = f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{FB_PAGE_ID}/videos"
    with open(video_path, "rb") as f:
        files = {"source": f}
        data = {"description": caption, "access_token": FB_PAGE_ACCESS_TOKEN}
        r = requests.post(url, data=data, files=files, timeout=600)

    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}

    if not r.ok:
        raise RuntimeError(f"Facebook post failed: {payload}")

    print("Facebook post success:", payload)
    return payload

# =========================
# MAIN
# =========================
def run_once():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(ASSET_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)

    news = get_news()
    if not news:
        print("No fresh news found. Nothing to create or post.")
        return False

    print("Selected news:", news["title"])
    print("Link:", news["link"])
    print("Image:", news.get("image_url"))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = safe_filename(news["title"])
    raw_image_path = os.path.join(ASSET_DIR, f"{stamp}_{base}.jpg")
    voice_path = os.path.join(ASSET_DIR, f"{stamp}_{base}.mp3")
    video_path = os.path.join(OUTPUT_DIR, f"{stamp}_{base}.mp4")

    if not download_image(news.get("image_url"), raw_image_path):
        print("No real image found. Using fallback background.")
        create_fallback_news_image(raw_image_path)

    script = make_script(news)
    print("Creating voice...")
    create_voice(script, voice_path)

    print("Creating video...")
    create_video(news, raw_image_path, voice_path, video_path)

    caption = facebook_caption(news)
    fb_result = post_video_to_facebook(video_path, caption)

    used = load_used()
    used.append(news["id"])
    save_used(used)

    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "news": news,
                "video": video_path,
                "caption": caption,
                "facebook": fb_result,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print("Video created:", video_path)
    print("Done.")
    return True


def main():
    if not RUN_FOREVER:
        run_once()
        return

    print(f"RUN_FOREVER=1 enabled. Running every {SCHEDULE_EVERY_MINUTES} minutes.")
    while True:
        try:
            run_once()
        except Exception as e:
            print("Run failed:", repr(e))

        sleep_seconds = max(5, SCHEDULE_EVERY_MINUTES * 60)
        print(f"Sleeping {sleep_seconds} seconds...")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
