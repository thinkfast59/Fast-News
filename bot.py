name: World Pulse Daily Auto News

on:
  workflow_dispatch:
  schedule:
    # Runs 5 times per day in UTC. Your PC can be offline.
    # Sri Lanka time: about 7:30 AM, 11:30 AM, 3:30 PM, 7:30 PM, 11:30 PM
    - cron: "0 2,6,10,14,18 * * *"

permissions:
  contents: read

jobs:
  create-and-post-news:
    runs-on: ubuntu-latest
    timeout-minutes: 45

    env:
      PAGE_NAME: WORLD PULSE DAILY
      LANGUAGE: en
      POST_TO_FACEBOOK: "1"
      US_NEWS_RATIO: "0.85"
      MAX_NEWS_AGE_HOURS: "72"
      FB_PAGE_ID: ${{ secrets.FB_PAGE_ID }}
      FB_PAGE_ACCESS_TOKEN: ${{ secrets.FB_PAGE_ACCESS_TOKEN }}

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Restore used-news memory
        uses: actions/cache@v4
        with:
          path: state
          key: used-news-state-${{ github.run_id }}
          restore-keys: |
            used-news-state-

      - name: Install system packages
        run: |
          sudo apt-get update
          sudo apt-get install -y ffmpeg fonts-dejavu-core

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Python packages
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run bot once
        run: python bot.py

      - name: Upload latest video artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: latest-news-video
          path: |
            output/*.mp4
            output/latest_news.json
          if-no-files-found: ignore
