"""Dreaming Spanish API helpers (uses your own login token, DREAMING_TOKEN in .env).

For videos your account can watch, the API returns:
  - sources.cloudflare: a signed Cloudflare Stream URL (HLS at <url>/manifest/video.m3u8)
  - sources.youtube:    the YouTube upload's id (free videos only)
  - subtitles:          human-made Spanish WebVTT

Signed URLs expire after a few hours, so fetch video info right before downloading.
"""

import json
import os
import re
import subprocess
import urllib.request

API = "https://app.dreaming.com/api"
UA = "Mozilla/5.0 (language-app prototype)"


class DreamingError(Exception):
    pass


def _get(path):
    token = os.environ.get("DREAMING_TOKEN")
    if not token:
        raise DreamingError("DREAMING_TOKEN is not set (put it in .env)")
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {token}", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise DreamingError("Dreaming Spanish rejected the login token (DREAMING_TOKEN); it may have expired")
        raise


def video(video_id):
    v = _get(f"/video?id={video_id}")["video"]
    if not v.get("hasAccess"):
        raise DreamingError(f"Your account can't watch this video ({video_id})")
    return v


def hls_url(v):
    cf = (v.get("sources") or {}).get("cloudflare")
    if not cf:
        raise DreamingError("No Cloudflare stream for this video")
    return cf.rstrip("/") + "/manifest/video.m3u8"


def download_audio(v, dest):
    """Save just the audio track of the video's HLS stream to dest (.m4a)."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", hls_url(v),
                    "-map", "0:a:0", "-c", "copy", str(dest)], check=True)
    return dest


def _ts(s):
    h, m, rest = ([0] + s.split(":"))[-3:]
    return int(h) * 3600 + int(m) * 60 + float(rest)


def subtitle_cues(v):
    """Parse the Spanish WebVTT into [{"text", "start", "end"}]."""
    vtt = v.get("subtitles") or ""
    cues = []
    for block in re.split(r"\n\s*\n", vtt.replace("\r", "")):
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            if "-->" in line:
                a, b = (x.strip().split()[0] for x in line.split("-->"))
                text = " ".join(l.strip() for l in lines[i + 1:])
                text = re.sub(r"<[^>]+>", "", text).strip()  # drop VTT styling tags
                if text:
                    cues.append({"text": text, "start": _ts(a), "end": _ts(b)})
                break
    return cues
