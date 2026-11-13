#!/usr/bin/env python3
"""Fetch the first N *free* videos of a Dreaming Spanish browse view and save them for the app.

Mirrors https://app.dreaming.com/spanish/browse?sort=easy&level=beginner :
the site loads a public catalog JSON, filters by level and sorts "easy" by
ascending difficultyScore. Members-only ("private") videos are skipped.

Each candidate is checked against the Dreaming Spanish API with your login
(DREAMING_TOKEN in .env): only videos your account can watch, with a stream and
Spanish subtitles, are kept. Conversion later takes audio and subtitles from there.

  python scrape_dreaming.py [--level beginner] [--count 10]

Writes web/data/videos.json and web/data/thumbs/<id>.jpg
"""

import argparse
import json
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

import dreaming

CATALOG = "https://app.dreaming.com/videos/prod/es.json"
THUMB = "https://d36f3pr6g3yfev.cloudfront.net/{id}.jpg"
OUT = Path(__file__).resolve().parent / "web" / "data"
UA = {"User-Agent": "Mozilla/5.0 (language-app prototype)"}

# Same mapping the site uses to turn the raw score into its 0-100 difficulty number
SCORE_MIN, SCORE_MAX = 200, 2600


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def difficulty(score):
    return round(min(100, max(0, (score - SCORE_MIN) * 100 / (SCORE_MAX - SCORE_MIN))))


def main():
    load_dotenv(Path(__file__).resolve().parent / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--level", default="beginner")
    p.add_argument("--count", type=int, default=10)
    args = p.parse_args()

    videos = json.loads(get(CATALOG))["videos"]
    candidates = sorted((v for v in videos if v["level"] == args.level and not v["private"]),
                        key=lambda v: v["difficultyScore"])

    (OUT / "thumbs").mkdir(parents=True, exist_ok=True)
    out = []
    for v in candidates:
        if len(out) >= args.count:
            break
        try:
            info = dreaming.video(v["_id"])
        except dreaming.DreamingError as e:
            print(f"  skip ({e}): {v['title']}")
            continue
        if not (info.get("sources") or {}).get("cloudflare") or not info.get("subtitles"):
            print(f"  skip (no stream or subtitles): {v['title']}")
            continue
        yt = (info.get("sources") or {}).get("youtube")
        thumb = OUT / "thumbs" / f"{v['_id']}.jpg"
        if not thumb.exists():
            thumb.write_bytes(get(THUMB.format(id=v["_id"])))
        out.append({
            "id": v["_id"],
            "title": v["title"],
            "duration": v["duration"] - (v.get("endCutout") or 0),  # seconds, as the site shows it
            "difficulty": difficulty(v["difficultyScore"]),
            "difficultyScore": v["difficultyScore"],
            "level": v["level"],
            "guides": v.get("guides", []),
            "thumbnail": f"data/thumbs/{v['_id']}.jpg",
            "source": f"dreaming:{v['_id']}",
            "youtube": f"https://www.youtube.com/watch?v={yt}" if yt else None,
        })
        o = out[-1]
        print(f"{o['difficulty']:3}  {o['duration'] // 60:2}:{o['duration'] % 60:02}  {v['title']}")

    # Drop thumbnails for videos no longer listed
    keep = {f"{o['id']}.jpg" for o in out}
    for f in (OUT / "thumbs").glob("*.jpg"):
        if f.name not in keep:
            f.unlink()

    (OUT / "videos.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"Saved {len(out)} videos to {OUT / 'videos.json'}")


if __name__ == "__main__":
    main()
