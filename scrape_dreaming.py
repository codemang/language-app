#!/usr/bin/env python3
"""Fetch the first N *free* videos of a Dreaming Spanish browse view and save them for the app.

Mirrors https://app.dreaming.com/spanish/browse?sort=easy&level=beginner :
the site loads a public catalog JSON, filters by level and sorts "easy" by
ascending difficultyScore. Members-only ("private") videos are skipped.

The Dreaming Spanish API only serves media to logged-in users, but free videos
are also published on their YouTube channel. Each one is matched to its
YouTube upload (title similarity + duration + channel), which is where the app
gets the audio for conversion. Videos without a confident match are skipped.

  python scrape_dreaming.py [--level beginner] [--count 10]

Writes web/data/videos.json and web/data/thumbs/<id>.jpg
"""

import argparse
import json
import re
import unicodedata
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

import yt_dlp

CATALOG = "https://app.dreaming.com/videos/prod/es.json"
THUMB = "https://d36f3pr6g3yfev.cloudfront.net/{id}.jpg"
DS_CHANNEL_ID = "UCouyFdE9-Lrjo3M_2idKq1A"
OUT = Path(__file__).resolve().parent / "web" / "data"
UA = {"User-Agent": "Mozilla/5.0 (language-app prototype)"}

# Same mapping the site uses to turn the raw score into its 0-100 difficulty number
SCORE_MIN, SCORE_MAX = 200, 2600

# YouTube uploads include intro/outro the app's duration excludes
DURATION_SLACK = (-30, 300)


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def difficulty(score):
    return round(min(100, max(0, (score - SCORE_MIN) * 100 / (SCORE_MAX - SCORE_MIN))))


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\((a\d.*?|level.*?|beginner|intermediate|advanced)\)", " ", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def core(title):
    """The distinctive part of a title: the quoted story name, else text before ' - ' / ':'."""
    m = re.search(r"[\"“”']([^\"“”]{4,})[\"“”]", title)
    if m:
        return norm(m.group(1))
    return norm(re.split(r" - |: ", title)[0])


def find_on_youtube(ydl, v):
    res = ydl.extract_info(f"ytsearch5:Dreaming Spanish {v['title']}", download=False)
    want, want_core = norm(v["title"]), core(v["title"])
    for e in res.get("entries") or []:
        if e.get("channel_id") and e["channel_id"] != DS_CHANNEL_ID:
            continue
        got = norm(e.get("title") or "")
        title_ok = SequenceMatcher(None, want, got).ratio() >= 0.85 or (len(want_core) >= 8 and want_core in got)
        d = (e.get("duration") or 0) - v["duration"]
        if title_ok and DURATION_SLACK[0] <= d <= DURATION_SLACK[1]:
            return e["id"]
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--level", default="beginner")
    p.add_argument("--count", type=int, default=10)
    args = p.parse_args()

    videos = json.loads(get(CATALOG))["videos"]
    candidates = sorted((v for v in videos if v["level"] == args.level and not v["private"]),
                        key=lambda v: v["difficultyScore"])

    (OUT / "thumbs").mkdir(parents=True, exist_ok=True)
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True})
    out = []
    for v in candidates:
        if len(out) >= args.count:
            break
        yt = find_on_youtube(ydl, v)
        if not yt:
            print(f"  skip (no YouTube match): {v['title']}")
            continue
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
            "youtube": f"https://www.youtube.com/watch?v={yt}",
        })
        o = out[-1]
        print(f"{o['difficulty']:3}  {o['duration'] // 60:2}:{o['duration'] % 60:02}  {v['title']}  ->  {yt}")

    # Drop thumbnails for videos no longer listed
    keep = {f"{o['id']}.jpg" for o in out}
    for f in (OUT / "thumbs").glob("*.jpg"):
        if f.name not in keep:
            f.unlink()

    (OUT / "videos.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"Saved {len(out)} videos to {OUT / 'videos.json'}")


if __name__ == "__main__":
    main()
