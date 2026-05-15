import os
import re
import json
import hashlib
import textwrap
import random
from datetime import datetime

import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from gtts import gTTS
from moviepy import (
    ImageClip,
    AudioFileClip,
    TextClip,
    CompositeVideoClip,
)

OUTPUT_DIR = "output"
USED_FILE = "used.json"

PAGE_NAME = "WORLD PULSE DAILY"
LANGUAGE = "en"  # Sinhala voice: try "si", English: "en"

VIDEO_SIZE = (1080, 1920)

FEEDS = [
    "https://www.bbc.com/news/world/rss.xml",
    "https://feeds.skynews.com/feeds/rss/world.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
]


def clean_text(text):
    text = BeautifulSoup(text or "", "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_used():
    if os.path.exists(USED_FILE):
        with open(USED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_used(used):
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(used[-300:], f, indent=2)


def get_news():
    used = load_used()
    all_items = []

    for feed_url in FEEDS:
        feed = feedparser.parse(feed_url)

        for item in feed.entries:
            title = clean_text(item.get("title", ""))
            summary = clean_text(item.get("summary", ""))
            link = item.get("link", "")

            if not title or not link:
                continue

            news_id = hashlib.md5(link.encode()).hexdigest()

            if news_id not in used:
                all_items.append({
                    "id": news_id,
                    "title": title,
                    "summary": summary,
                    "link": link
                })

    if not all_items:
        return None

    news = random.choice(all_items)

    used.append(news["id"])
    save_used(used)

    return news


def make_script(news):
    title = news["title"]
    summary = news["summary"]

    if not summary:
        summary = title

    return (
        f"{title}. "
        f"{summary}. "
        f"Stay tuned for more updates."
    )


def get_font(size, bold=False):
    possible_fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]

    for font in possible_fonts:
        try:
            return ImageFont.truetype(font, size)
        except:
            pass

    return ImageFont.load_default()


def create_background(news, path):
    img = Image.new("RGB", VIDEO_SIZE, (10, 18, 35))
    draw = ImageDraw.Draw(img)

    title_font = get_font(58, bold=True)
    small_font = get_font(32, bold=False)

    colors = [
        ((15, 32, 70), (30, 90, 160)),
        ((30, 20, 65), (95, 40, 150)),
        ((15, 50, 55), (20, 120, 130)),
        ((55, 20, 35), (150, 35, 70)),
    ]

    top_color, bottom_color = random.choice(colors)

    for y in range(VIDEO_SIZE[1]):
        ratio = y / VIDEO_SIZE[1]
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        draw.line([(0, y), (VIDEO_SIZE[0], y)], fill=(r, g, b))

    draw.rectangle([0, 0, 1080, 230], fill=(0, 0, 0, 90))
    draw.text((60, 75), PAGE_NAME, fill="white", font=title_font)

    draw.rounded_rectangle(
        [60, 290, 1020, 460],
        radius=35,
        fill=(255, 255, 255)
    )

    draw.text((95, 340), "BREAKING NEWS UPDATE", fill=(20, 30, 60), font=title_font)

    draw.text(
        (60, 1810),
        datetime.now().strftime("%Y-%m-%d"),
        fill=(230, 230, 230),
        font=small_font
    )

    img.save(path)


def create_voice(script, path):
    tts = gTTS(text=script, lang=LANGUAGE, slow=False)
    tts.save(path)


def create_video(image_path, audio_path, output_path, news):
    audio = AudioFileClip(audio_path)
    duration = audio.duration

    bg = ImageClip(image_path).with_duration(duration)

    title = news["title"]
    summary = news["summary"] or ""

    video_text = f"{title}\n\n{summary}"
    video_text = video_text[:700]

    title_clip = (
        TextClip(
            text=title,
            font_size=62,
            color="white",
            size=(960, None),
            method="caption",
        )
        .with_duration(duration)
        .with_position(("center", 560))
    )

    moving_clip = (
        TextClip(
            text=video_text,
            font_size=46,
            color="white",
            size=(900, None),
            method="caption",
        )
        .with_duration(duration)
        .with_position(lambda t: ("center", int(1500 - t * 55)))
    )

    source_clip = (
        TextClip(
            text="Source: Public news feed",
            font_size=30,
            color="white",
            size=(900, None),
            method="caption",
        )
        .with_duration(duration)
        .with_position(("center", 1740))
    )

    final = CompositeVideoClip(
        [bg, title_clip, moving_clip, source_clip],
        size=VIDEO_SIZE
    )

    final = final.with_audio(audio)

    final.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        preset="medium"
    )

    audio.close()
    final.close()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    news = get_news()

    if not news:
        print("No new news found.")
        return

    script = make_script(news)

    image_path = os.path.join(OUTPUT_DIR, "background.jpg")
    audio_path = os.path.join(OUTPUT_DIR, "voice.mp3")
    video_path = os.path.join(OUTPUT_DIR, "auto_video.mp4")

    create_background(news, image_path)
    create_voice(script, audio_path)
    create_video(image_path, audio_path, video_path, news)

    print("DONE:", video_path)
    print("NEWS:", news["title"])
    print("LINK:", news["link"])


if __name__ == "__main__":
    main()
