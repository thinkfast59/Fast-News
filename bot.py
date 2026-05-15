import os
import re
import json
import hashlib
import textwrap
import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from gtts import gTTS
from moviepy import ImageClip, AudioFileClip

OUTPUT_DIR = "output"
USED_FILE = "used.json"

FEEDS = [
    "https://www.bbc.com/news/world/rss.xml",
    "https://feeds.reuters.com/reuters/worldNews",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
]

VIDEO_SIZE = (1080, 1920)
LANGUAGE = "en"  # change to "si" for Sinhala voice if supported


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
        json.dump(used[-100:], f, indent=2)


def get_news():
    used = load_used()

    for feed_url in FEEDS:
        feed = feedparser.parse(feed_url)

        for item in feed.entries:
            title = clean_text(item.get("title", ""))
            summary = clean_text(item.get("summary", ""))
            link = item.get("link", "")

            if not title:
                continue

            news_id = hashlib.md5(link.encode()).hexdigest()

            if news_id not in used:
                used.append(news_id)
                save_used(used)

                return {
                    "title": title,
                    "summary": summary[:350],
                    "link": link
                }

    return None


def make_script(news):
    return (
        f"Today update. {news['title']}. "
        f"{news['summary']} "
        f"This information was collected from public news RSS sources."
    )


def create_background(title, summary, path):
    img = Image.new("RGB", VIDEO_SIZE, (15, 25, 45))
    draw = ImageDraw.Draw(img)

    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 58)
        body_font = ImageFont.truetype("DejaVuSans.ttf", 38)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 30)
    except:
        title_font = body_font = small_font = None

    draw.rectangle([0, 0, 1080, 260], fill=(30, 60, 120))
    draw.text((60, 80), "WORLD PULSE DAILY", fill="white", font=title_font)

    y = 360
    for line in textwrap.wrap(title, width=24):
        draw.text((60, y), line, fill="white", font=title_font)
        y += 75

    y += 40
    for line in textwrap.wrap(summary, width=34):
        draw.text((60, y), line, fill=(230, 230, 230), font=body_font)
        y += 55

    draw.text((60, 1760), "Auto generated from public RSS news", fill=(200, 200, 200), font=small_font)

    img.save(path)


def create_voice(script, path):
    tts = gTTS(text=script, lang=LANGUAGE, slow=False)
    tts.save(path)


def create_video(image_path, audio_path, output_path):
    audio = AudioFileClip(audio_path)
    clip = ImageClip(image_path).with_duration(audio.duration)
    clip = clip.with_audio(audio)
    clip.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac"
    )


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

    create_background(news["title"], news["summary"], image_path)
    create_voice(script, audio_path)
    create_video(image_path, audio_path, video_path)

    print("Video created:", video_path)
    print("News source:", news["link"])


if __name__ == "__main__":
    main()
