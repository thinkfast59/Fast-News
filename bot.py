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
MAX_SCRIPT_CHARS = int(os.getenv("MAX_SCRIPT_CHARS", "900"))
MIN_IMAGE_SIZE = int(os.getenv("MIN_IMAGE_SIZE", "240"))

US_NEWS_RATIO = float(os.getenv("US_NEWS_RATIO", "0.65"))

RUN_FOREVER = os.getenv("RUN_FOREVER", "0") == "1"
RUN_EVERY_MINUTES = int(os.getenv("RUN_EVERY_MINUTES", "240"))

HIDE_IMAGE_CORNER_LOGOS = os.getenv("HIDE_IMAGE_CORNER_LOGOS", "1") == "1"
SHOW_SOURCE_TEXT = os.getenv("SHOW_SOURCE_TEXT", "0") == "1"

POST_TO_FACEBOOK = os.getenv("POST_TO_FACEBOOK", "1") == "1"
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
FB_GRAPH_VERSION = os.getenv("FB_GRAPH_VERSION", "v25.0")

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 Windows NT 10.0 Win64 x64 WorldPulseDailyBot/3.0",
)


US_FEEDS = [
    "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    "https://feeds.npr.org/1001/rss.xml",
    "https://feeds.npr.org/1014/rss.xml",
    "https://www.pbs.org/newshour/feeds/rss/headlines",
    "https://abcnews.go.com/abcnews/usheadlines",
    "https://abcnews.go.com/abcnews/politicsheadlines",
    "https://www.cbsnews.com/latest/rss/us",
    "https://www.cbsnews.com/latest/rss/politics",
    "https://www.nbcnews.com/id/3032525/device/rss/rss.xml",
    "https://www.nbcnews.com/id/3032553/device/rss/rss.xml",
    "https://www.usnews.com/rss/news",
    "https://thehill.com/feed/",
    "https://www.politico.com/rss/politicopicks.xml",
    "https://feeds.washingtonpost.com/rss/national",
]

WORLD_FEEDS = [
    "https://feeds.skynews.com/feeds/rss/world.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://feeds.npr.org/1004/rss.xml",
    "https://www.france24.com/en/rss",
    "https://www.dw.com/en/top-stories/s-9097?maca=en-rss-en-all-1573-rdf",
    "https://www.theguardian.com/world/rss",
    "https://www.cbc.ca/cmlink/rss-world",
    "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://www.theguardian.com/technology/rss",
    "https://www.theguardian.com/science/rss",
]


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


def load_used() -> list:
    if os.path.exists(USED_FILE):
        try:
            with open(USED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_used(used: list) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(used[-2000:], f, indent=2, ensure_ascii=False)


def get_font(size: int, bold: bool = False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansSinhala-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansSinhala-Regular.ttf",
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
    lines = []
    current = ""

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


def add_dark_gradient(img):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for y in range(VIDEO_HEIGHT):
        if y < 650:
            alpha = int(150 - y * 0.11)
        elif y > 1050:
            alpha = int(60 + 170 * ((y - 1050) / 870))
        else:
            alpha = 38

        draw.line(
            [(0, y), (VIDEO_WIDTH, y)],
            fill=(0, 0, 0, max(0, min(235, alpha))),
        )

    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def blur_corner_logos(img):
    if not HIDE_IMAGE_CORNER_LOGOS:
        return img

    img = img.convert("RGB")
    w, h = img.size

    boxes = [
        (0, 0, int(w * 0.25), int(h * 0.15)),
        (int(w * 0.75), 0, w, int(h * 0.15)),
        (0, int(h * 0.85), int(w * 0.25), h),
        (int(w * 0.75), int(h * 0.85), w, h),
    ]

    for box in boxes:
        crop = img.crop(box).filter(ImageFilter.GaussianBlur(radius=20))
        img.paste(crop, box)

    return img


def create_fallback_news_image(path):
    img = Image.new("RGB", VIDEO_SIZE, (5, 12, 30))
    draw = ImageDraw.Draw(img)

    for y in range(VIDEO_HEIGHT):
        ratio = y / VIDEO_HEIGHT
        fill = (
            int(6 + 25 * ratio),
            int(14 + 30 * ratio),
            int(40 + 80 * ratio),
        )
        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=fill)

    draw.text((80, 720), "WORLD", font=get_font(95, True), fill="white")
    draw.text((80, 835), "NEWS", font=get_font(100, True), fill=(255, 45, 45))
    draw.text((80, 980), "UPDATE", font=get_font(52, True), fill=(230, 235, 245))

    img.save(path, quality=95)


def upgrade_image_url(url):
    if not url:
        return None

    upgraded = url

    for old in [
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


def parse_entry_time(entry):
    raw = entry.get("published") or entry.get("updated") or ""

    if raw:
        try:
            return parsedate_to_datetime(raw).astimezone(timezone.utc)
        except Exception:
            pass

    return datetime.now(timezone.utc)


def pick_feed_group():
    if random.random() < US_NEWS_RATIO:
        print("Feed mode: US news")
        return US_FEEDS

    print("Feed mode: World news")
    return WORLD_FEEDS


def get_news():
    used = set(load_used())

    feed_group = pick_feed_group()
    feeds = feed_group[:]
    random.shuffle(feeds)

    candidates = []

    for feed_url in feeds:
        try:
            print("Checking feed:", feed_url)
            feed = feedparser.parse(feed_url)
            source_name = clean_text(feed.feed.get("title", "News Source"))

            entries = list(feed.entries[:25])
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

                candidates.append(
                    {
                        "id": news_id,
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "image_url": get_image_from_feed_entry(entry),
                        "source": source_name,
                        "feed_url": feed_url,
                        "published_at": parse_entry_time(entry).isoformat(),
                    }
                )

        except Exception as e:
            print("Feed error:", feed_url, e)

    if not candidates:
        print("No unused news in selected group. Trying all feeds.")

        all_feeds = US_FEEDS + WORLD_FEEDS
        random.shuffle(all_feeds)

        for feed_url in all_feeds:
            try:
                feed = feedparser.parse(feed_url)
                source_name = clean_text(feed.feed.get("title", "News Source"))

                for entry in feed.entries[:25]:
                    title = clean_text(entry.get("title", ""))
                    summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
                    link = entry.get("link", "")

                    if not title or not link:
                        continue

                    news_id = hashlib.sha256(link.encode("utf-8")).hexdigest()

                    if news_id in used:
                        continue

                    candidates.append(
                        {
                            "id": news_id,
                            "title": title,
                            "summary": summary,
                            "link": link,
                            "image_url": get_image_from_feed_entry(entry),
                            "source": source_name,
                            "feed_url": feed_url,
                            "published_at": parse_entry_time(entry).isoformat(),
                        }
                    )

            except Exception as e:
                print("Backup feed error:", feed_url, e)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (bool(x.get("image_url")), x.get("published_at", "")),
        reverse=True,
    )

    top_pool = candidates[:20] if len(candidates) > 20 else candidates
    news = random.choice(top_pool)

    article_image = get_image_from_article_page(news["link"])

    if article_image:
        news["image_url"] = article_image
    elif news.get("image_url"):
        news["image_url"] = upgrade_image_url(news["image_url"])

    print("Selected source:", news["source"])
    print("Selected title:", news["title"])

    return news


def make_script(news):
    title = shorten(news["title"], 180)
    summary = shorten(news.get("summary", ""), MAX_SCRIPT_CHARS)

    openings = [
        "Here is a major news update.",
        "This is the latest news update.",
        "Here is what is happening now.",
        "A new update is coming in.",
        "This story is developing.",
    ]

    transitions = [
        "Here is what we know.",
        "According to the latest information.",
        "The report says.",
        "The key details are these.",
        "Here are the important points.",
    ]

    endings = [
        "Follow World Pulse Daily for more updates.",
        "Stay with World Pulse Daily for the latest news.",
        "For more updates, follow World Pulse Daily.",
        "World Pulse Daily will bring you more updates soon.",
    ]

    script = f"{random.choice(openings)} {title}. "

    if summary:
        script += f"{random.choice(transitions)} {summary}. "
    else:
        script += "More details are expected soon. "

    script += random.choice(endings)

    return script


def create_voice(script, path):
    tts = gTTS(text=script, lang=LANGUAGE, slow=False)
    tts.save(path)


def ease_out_back(x):
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * pow(x - 1, 3) + c1 * pow(x - 1, 2)


def smoothstep(x):
    x = max(0, min(1, x))
    return x * x * (3 - 2 * x)


def draw_rounded_panel(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def split_words(script):
    return [w.strip() for w in script.split() if w.strip()]


def get_spoken_words(script, t, duration, max_words=28):
    words = split_words(script)

    if not words:
        return [], -1

    progress = min(1.0, max(0.0, t / max(duration, 1)))
    current_index = min(len(words) - 1, int(progress * len(words)))

    start = max(0, current_index - 8)
    end = min(len(words), start + max_words)

    if end - start < max_words:
        start = max(0, end - max_words)

    return words[start:end], current_index - start


def draw_glow_text(draw, pos, text, font, fill, glow_fill, glow_radius=2):
    x, y = pos

    for dx in range(-glow_radius, glow_radius + 1):
        for dy in range(-glow_radius, glow_radius + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=glow_fill)

    draw.text((x, y), text, font=font, fill=fill)


def draw_animated_header(draw, t):
    pulse = (np.sin(t * 4.0) + 1) / 2

    draw.rectangle((0, 0, VIDEO_WIDTH, 175), fill=(3, 8, 20, 245))

    x_shift = int(12 * np.sin(t * 1.8))

    draw.text((50 + x_shift, 42), PAGE_NAME, font=get_font(58, True), fill="white")

    draw.rounded_rectangle(
        (770, 48, 1030, 120),
        radius=24,
        fill=(190, 18, 32, 230),
    )

    dot_alpha = int(150 + 105 * pulse)
    draw.ellipse((795, 72, 825, 102), fill=(255, 255, 255, dot_alpha))

    draw.text((845, 67), "LIVE", font=get_font(35, True), fill="white")

    draw.text(
        (770, 128),
        datetime.now().strftime("%Y-%m-%d"),
        font=get_font(25),
        fill=(215, 220, 235),
    )


def draw_breaking_bar(draw, t):
    slide = min(1, t / 0.8)
    x1 = int(-1050 + 1090 * ease_out_back(slide))

    draw_rounded_panel(
        draw,
        (x1, 205, x1 + 980, 315),
        28,
        fill=(190, 18, 32, 245),
    )

    shimmer_x = int((t * 240) % 1100)

    draw.rectangle(
        (x1 + shimmer_x - 140, 205, x1 + shimmer_x - 60, 315),
        fill=(255, 255, 255, 45),
    )

    draw.text((x1 + 42, 234), "BREAKING NEWS UPDATE", font=get_font(45, True), fill="white")
    draw.text((x1 + 825, 236), "ON AIR", font=get_font(31, True), fill=(255, 240, 80))


def draw_photo_panel(img, original, t, duration):
    draw = ImageDraw.Draw(img)

    photo_box = (50, 360, 1030, 1085)
    photo_w = photo_box[2] - photo_box[0]
    photo_h = photo_box[3] - photo_box[1]

    slide = smoothstep(min(1, t / 1.2))
    photo_y_offset = int((1 - slide) * 80)

    photo = cover_resize(original, (photo_w, photo_h)).filter(ImageFilter.SHARPEN)
    photo = blur_corner_logos(photo)

    mask = Image.new("L", (photo_w, photo_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, photo_w, photo_h), radius=38, fill=255)

    img.paste(photo.convert("RGBA"), (photo_box[0], photo_box[1] + photo_y_offset), mask)

    draw.rounded_rectangle(
        (photo_box[0], photo_box[1] + photo_y_offset, photo_box[2], photo_box[3] + photo_y_offset),
        radius=38,
        outline=(255, 255, 255, 95),
        width=3,
    )

    scan_y = int(photo_box[1] + 20 + ((t * 85) % photo_h))
    draw.rectangle(
        (photo_box[0] + 20, scan_y, photo_box[2] - 20, scan_y + 5),
        fill=(255, 255, 255, 85),
    )


def draw_rolling_words(draw, spoken_words, active_word_index, panel_top, t):
    x_start = 75
    y = panel_top + 108
    max_width = 900

    normal_font = get_font(44, True)
    active_font = get_font(60, True)

    lines = []
    current = []

    for i, word in enumerate(spoken_words):
        test = " ".join([w for _, w in current] + [word])
        w, _ = text_size(draw, test, normal_font)

        if w <= max_width:
            current.append((i, word))
        else:
            if current:
                lines.append(current)
            current = [(i, word)]

    if current:
        lines.append(current)

    for line_num, line in enumerate(lines[:6]):
        x = x_start

        for i, word in line:
            is_active = i == active_word_index

            word_font = active_font if is_active else normal_font
            fill = (255, 240, 65) if is_active else (235, 240, 250)

            bounce = int(12 * abs(np.sin(t * 10))) if is_active else 0
            word_y = y - bounce if is_active else y

            if is_active:
                bbox = draw.textbbox((x - 12, word_y - 8), word, font=word_font)
                draw.rounded_rectangle(
                    (bbox[0], bbox[1], bbox[2] + 16, bbox[3] + 12),
                    radius=18,
                    fill=(220, 35, 45, 235),
                )

                draw.rounded_rectangle(
                    (bbox[0] - 4, bbox[1] - 4, bbox[2] + 20, bbox[3] + 16),
                    radius=22,
                    outline=(255, 255, 255, 110),
                    width=3,
                )

            draw_glow_text(
                draw,
                (x, word_y),
                word,
                word_font,
                fill,
                (0, 0, 0, 120),
                glow_radius=2,
            )

            ww, _ = text_size(draw, word + " ", word_font)
            x += ww + 8

        y += 82


def draw_title_card(draw, news, panel_top, t):
    appear = smoothstep(min(1, t / 1.0))
    y_offset = int((1 - appear) * 90)

    title = shorten(news["title"], 105)
    title_font = get_font(45, True)
    lines = wrap_text(draw, title, title_font, 900)

    draw.text(
        (75, panel_top + 35 + y_offset),
        "NOW SPEAKING",
        font=get_font(27, True),
        fill=(255, 75, 75),
    )

    y = panel_top + 72 + y_offset

    for line in lines[:3]:
        draw_glow_text(
            draw,
            (75, y),
            line,
            title_font,
            "white",
            (0, 0, 0, 160),
            glow_radius=2,
        )
        y += 56


def create_news_frame(news, image_path, script, t, duration):
    original = Image.open(image_path).convert("RGB")

    progress = min(1.0, t / max(duration, 1))
    zoom = 1.0 + progress * 0.045 + 0.01 * np.sin(t * 0.8)

    crop_w = int(original.width / zoom)
    crop_h = int(original.height / zoom)

    left = max(0, (original.width - crop_w) // 2)
    top = max(0, (original.height - crop_h) // 2)

    original = original.crop((left, top, left + crop_w, top + crop_h))

    bg = cover_resize(original, VIDEO_SIZE).filter(ImageFilter.GaussianBlur(radius=22))
    bg = add_dark_gradient(bg)

    img = bg.convert("RGBA")
    draw = ImageDraw.Draw(img)

    draw_animated_header(draw, t)
    draw_breaking_bar(draw, t)
    draw_photo_panel(img, original, t, duration)

    panel_top = 1125
    panel_bottom = 1870

    panel_slide = smoothstep(min(1, t / 1.2))
    panel_y = int(panel_top + (1 - panel_slide) * 160)

    draw_rounded_panel(
        draw,
        (40, panel_y, 1040, panel_bottom),
        38,
        fill=(5, 12, 28, 235),
        outline=(255, 255, 255, 70),
        width=2,
    )

    pulse = int(120 + 80 * ((np.sin(t * 5) + 1) / 2))

    draw.rounded_rectangle(
        (75, panel_y + 42, 250, panel_y + 58),
        radius=8,
        fill=(235, 30, 45, pulse),
    )

    spoken_words, active_word_index = get_spoken_words(script, t, duration, max_words=28)

    draw_title_card(draw, news, panel_y, t)
    draw_rolling_words(draw, spoken_words, active_word_index, panel_y + 225, t)

    if SHOW_SOURCE_TEXT:
        draw.text(
            (75, 1815),
            f"Source: {shorten(news.get('source', ''), 65)}",
            font=get_font(25),
            fill=(200, 205, 215),
        )

    return img.convert("RGB")


def create_video(news, image_path, audio_path, output_path, script):
    audio = AudioFileClip(audio_path)
    duration = min(max(audio.duration, 8), 90)

    def make_frame(t):
        return np.array(create_news_frame(news, image_path, script, t, duration))

    video = VideoClip(make_frame, duration=duration)

    try:
        video = video.with_audio(audio)
    except Exception:
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

    hashtags = [
        "#WorldPulseDaily",
        "#USNews",
        "#WorldNews",
        "#BreakingNews",
        "#NewsUpdate",
        "#LatestNews",
    ]

    return (
        f"{title}\n\n"
        "Watch the latest update from World Pulse Daily.\n\n"
        + " ".join(hashtags)
    )


def post_video_to_facebook(video_path, caption):
    if not POST_TO_FACEBOOK:
        print("Facebook posting disabled by POST_TO_FACEBOOK=0")
        return None

    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        print("Facebook posting skipped: missing FB_PAGE_ID or FB_PAGE_ACCESS_TOKEN.")
        return None

    url = f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{FB_PAGE_ID}/videos"

    with open(video_path, "rb") as f:
        files = {"source": f}
        data = {
            "description": caption,
            "access_token": FB_PAGE_ACCESS_TOKEN,
        }

        r = requests.post(url, data=data, files=files, timeout=600)

    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}

    if not r.ok:
        raise RuntimeError(f"Facebook post failed: {payload}")

    print("Facebook post success:", payload)
    return payload


def run_once():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(ASSET_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)

    news = get_news()

    if not news:
        print("No fresh news found. Nothing to create or post.")
        return

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

    print("Voice script:")
    print(script)

    print("Creating voice...")
    create_voice(script, voice_path)

    print("Creating animated video...")
    create_video(news, raw_image_path, voice_path, video_path, script)

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
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print("Video created:", video_path)
    print("Done.")


def main():
    if RUN_FOREVER:
        print(f"RUN_FOREVER=1 enabled. Running every {RUN_EVERY_MINUTES} minutes.")

        while True:
            try:
                run_once()
            except Exception as e:
                print("Run error:", e)

            print(f"Sleeping {RUN_EVERY_MINUTES} minutes...")
            time.sleep(RUN_EVERY_MINUTES * 60)
    else:
        run_once()


if __name__ == "__main__":
    main()
