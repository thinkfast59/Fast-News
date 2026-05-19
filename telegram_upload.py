import os
import json
import requests

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
LATEST_FILE = os.path.join(OUTPUT_DIR, "latest_news.json")

TELEGRAM_BOT_TOKEN = os.getenv("8608459484:AAHvkzg6ZhYuClxlWHnG7syQa6e5n-3qoRk", "").strip()
TELEGRAM_CHAT_ID = os.getenv("8376417027", "").strip()


def send_video(video_path, caption):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_CHAT_ID")

    if not os.path.exists(video_path):
        raise RuntimeError(f"Video file not found: {video_path}")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"

    with open(video_path, "rb") as video:
        files = {
            "video": video
        }

        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption[:1024],
            "supports_streaming": "true"
        }

        response = requests.post(
            url,
            data=data,
            files=files,
            timeout=600
        )

    try:
        result = response.json()
    except Exception:
        result = {"raw": response.text}

    if not response.ok or not result.get("ok", False):
        raise RuntimeError(f"Telegram upload failed: {result}")

    print("Telegram video uploaded successfully.")
    print(result)
    return result


def main():
    if not os.path.exists(LATEST_FILE):
        raise RuntimeError(f"latest_news.json not found: {LATEST_FILE}")

    with open(LATEST_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    video_path = data.get("video")
    caption = data.get("caption", "Latest news update")

    send_video(video_path, caption)


if __name__ == "__main__":
    main()
