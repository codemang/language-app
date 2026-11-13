"""Keeps the app's audio under a size cap (default 3 GB, AUDIO_CAP_GB in .env).

Counted: finished lessons (web/lessons/<id>/) and the conversion cache (work/<id>/).
When a conversion needs room, space is freed in this order:
  1. the conversion cache of videos that are already converted (only needed to
     reconvert), least-recently played first
  2. whole lessons, least-recently played first; they show as "not converted" again

"Played" = the last time the app streamed that lesson's audio (work/played.json),
falling back to when the lesson was built. Lessons played in the last
RECENT_PLAY_SEC are never deleted, nor is the video being converted.
"""

import json
import os
import shutil
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LESSONS = ROOT / "web" / "lessons"
WORK = ROOT / "work"
PLAYED = WORK / "played.json"
NOT_VIDEOS = {"jobs"}  # folders under work/ that aren't a video's cache

# Measured per minute of video (see docs/storage-plan.md)
LESSON_MB_PER_MIN = 1.8
CACHE_MB_PER_MIN = 2.3
CONVERT_PEAK_MB_PER_MIN = 5.5  # decoded WAV + cut clips, deleted after each conversion
RECENT_PLAY_SEC = 30 * 60
SAVE_PLAYED_EVERY_SEC = 60

_lock = threading.Lock()


def cap_bytes():
    return int(float(os.environ.get("AUDIO_CAP_GB", "3")) * 1024**3)


def _size(path):
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


def _video_dirs(base):
    return [d for d in base.iterdir() if d.is_dir() and d.name not in NOT_VIDEOS] if base.exists() else []


def usage():
    return sum(_size(d) for d in _video_dirs(LESSONS) + _video_dirs(WORK))


def needed_bytes(duration_sec):
    """What converting a video of this length will need at its peak."""
    mb = (LESSON_MB_PER_MIN + CACHE_MB_PER_MIN + CONVERT_PEAK_MB_PER_MIN) * duration_sec / 60
    return int(mb * 1024**2)


# ---------------------------------------------------------------- play tracking

def _load_played():
    try:
        return json.loads(PLAYED.read_text())
    except (OSError, ValueError):
        return {}


def mark_played(lesson_id):
    """Called when the app streams a lesson's audio. Writes at most once a minute per lesson."""
    now = time.time()
    with _lock:
        played = _load_played()
        if now - played.get(lesson_id, 0) < SAVE_PLAYED_EVERY_SEC:
            return
        played[lesson_id] = now
        tmp = PLAYED.with_suffix(".tmp")
        tmp.write_text(json.dumps(played))
        tmp.replace(PLAYED)


def _last_played(lesson_id, played):
    if lesson_id in played:
        return played[lesson_id]
    lj = LESSONS / lesson_id / "lesson.json"
    return lj.stat().st_mtime if lj.exists() else 0


# ---------------------------------------------------------------- eviction

def make_room(needed=0, protect=(), log=print):
    """Delete least-recently-played data until usage + needed fits under the cap.
    Returns the list of lesson ids that were deleted."""
    with _lock:
        cap, used = cap_bytes(), usage()
        if used + needed <= cap:
            return []
        played = _load_played()
        now = time.time()
        protected = set(protect) | {k for k, t in played.items() if now - t < RECENT_PLAY_SEC}
        by_age = lambda d: _last_played(d.name, played)  # noqa: E731

        # 1. caches of converted (or failed / abandoned) videos; reconverting re-downloads
        caches = sorted((d for d in _video_dirs(WORK) if d.name not in protected), key=by_age)
        for d in caches:
            if used + needed <= cap:
                return []
            used -= _size(d)
            shutil.rmtree(d, ignore_errors=True)
            log(f"storage: removed conversion cache {d.name}")

        # 2. whole lessons
        deleted = []
        for d in sorted((d for d in _video_dirs(LESSONS) if d.name not in protected), key=by_age):
            if used + needed <= cap:
                break
            used -= _size(d)
            shutil.rmtree(d, ignore_errors=True)
            deleted.append(d.name)
            log(f"storage: deleted lesson {d.name} (least recently played)")
        if deleted:
            index = LESSONS / "index.json"
            try:
                kept = [l for l in json.loads(index.read_text()) if l["id"] not in deleted]
                index.write_text(json.dumps(kept, ensure_ascii=False, indent=2))
            except (OSError, ValueError):
                pass
        if used + needed > cap:
            log(f"storage: still over the cap ({used / 1024**3:.2f} GB used); everything left is protected")
        return deleted


def status():
    return {"used": usage(), "cap": cap_bytes()}
