"""Keeps the top N unwatched videos converted, in the background.

Targets: the first AUTO_CONVERT_COUNT videos of the app's list (easiest first) that
aren't watched, skipping videos longer than AUTO_MAX_MINUTES. Missing ones are
converted one at a time with AUTO_ENGINE (Kokoro voice) and --bulk download
spacing, at low CPU priority. Videos converted from the app always go first.

Guards:
  - Claude translation spend from all conversions this calendar month
    (state/spend.jsonl) must stay under AUTO_MONTHLY_BUDGET_USD.
  - Only watched lessons are deleted to make room; if that isn't enough it waits.
  - A failed video is skipped for FAIL_RETRY_SEC. An expired Dreaming Spanish
    login pauses everything until auto-convert is switched off and on again.

Settings (.env): AUTO_CONVERT_COUNT=20, AUTO_MAX_MINUTES=30,
AUTO_MONTHLY_BUDGET_USD=1, AUTO_ENGINE=kokoro.
On/off state: state/autopilot.json (switched from the app).
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import progress
import storage

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state" / "autopilot.json"
SPEND_LOG = ROOT / "state" / "spend.jsonl"
VIDEOS = ROOT / "web" / "data" / "videos.json"
LESSONS = ROOT / "web" / "lessons"
TICK_SEC = 10 * 60
FAIL_RETRY_SEC = 24 * 3600
TOKEN_ERROR = "rejected the login token"


def _env(name, default, cast):
    try:
        return cast(os.environ.get(name, default))
    except ValueError:
        return cast(default)


def month_spend():
    """Claude translation spend (USD) this calendar month, from all conversions."""
    start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    total = 0.0
    try:
        for line in SPEND_LOG.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("time", 0) >= start:
                total += rec.get("usd", 0)
    except OSError:
        pass
    return total


class Autopilot:
    def __init__(self, converter):
        self.conv = converter
        self.lock = threading.Lock()
        self.wake_event = threading.Event()
        self.count = _env("AUTO_CONVERT_COUNT", "20", int)
        self.max_sec = _env("AUTO_MAX_MINUTES", "30", float) * 60
        self.budget = _env("AUTO_MONTHLY_BUDGET_USD", "1", float)
        self.engine = os.environ.get("AUTO_ENGINE", "kokoro")
        st = self._load()
        self.enabled = st.get("enabled", True)
        self.failures = st.get("failures", {})  # video id -> time of last failure
        self.paused = st.get("paused")          # None | "token" (needs the switch)
        self.waiting = None                     # transient: "budget" | "storage"
        converter.on_finish = self._job_finished
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- persistence

    def _load(self):
        try:
            return json.loads(STATE.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"enabled": self.enabled, "failures": self.failures, "paused": self.paused}))
        tmp.replace(STATE)

    # ---- what to convert

    @staticmethod
    def _videos():
        try:
            return json.loads(VIDEOS.read_text())
        except (OSError, ValueError):
            return []

    def targets(self, videos=None):
        watched = {k for k, v in progress.load().items() if v.get("watched")}
        out = []
        for v in videos if videos is not None else self._videos():
            if len(out) >= self.count:
                break
            if v["id"] in watched or (v.get("duration") or 0) > self.max_sec:
                continue
            out.append(v)
        return out

    @staticmethod
    def ready(video_id):
        return (LESSONS / video_id / "lesson.json").exists()

    # ---- control

    def wake(self):
        self.wake_event.set()

    def set_enabled(self, on):
        with self.lock:
            self.enabled = bool(on)
            if on:
                self.paused = None  # switching on again retries after a token error
                self.failures = {}
            self._save()
        if not on:
            self.conv.cancel_auto()
        self.wake()
        return self.status()

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception as e:  # never let the planner thread die
                print(f"autopilot: {e!r}")
            self.wake_event.wait(TICK_SEC)
            self.wake_event.clear()

    def tick(self):
        with self.lock:
            if not self.enabled or self.paused:
                return
            if self.conv.has_auto_job():
                return
            if month_spend() >= self.budget:
                self.waiting = "budget"
                return
            now = time.time()
            targets = self.targets()
            target_ids = {v["id"] for v in targets}
            for v in targets:
                if self.ready(v["id"]) or self.conv.is_busy(v["id"]):
                    continue
                if now - self.failures.get(v["id"], 0) < FAIL_RETRY_SEC:
                    continue
                watched = {k for k, p in progress.load().items() if p.get("watched")}
                if not storage.make_room(storage.needed_bytes(v.get("duration") or 0),
                                         protect=target_ids, watched=watched, only_watched=True):
                    self.waiting = "storage"
                    return
                self.waiting = None
                self.conv.start(v["id"], self.engine, source="auto")
                return  # one at a time; the next tick picks the next one
            self.waiting = None

    def _job_finished(self, video_id, source, ok, log_text):
        if source != "auto":
            self.wake()  # a UI job may have freed the queue
            return
        with self.lock:
            if not ok and video_id is not None:
                if TOKEN_ERROR in log_text:
                    self.paused = "token"
                elif "cancelled" not in log_text:
                    self.failures[video_id] = time.time()
                self._save()
        self.wake()

    # ---- for the app

    def status(self):
        videos = self._videos()
        targets = self.targets(videos)
        ids = [v["id"] for v in targets]
        reason = None
        if self.paused == "token":
            reason = "Dreaming Spanish login expired: update DREAMING_TOKEN in .env, then switch auto-convert off and on."
        elif self.waiting == "budget":
            reason = f"Paused: this month's translation budget (${self.budget:.2f}) is used up."
        elif self.waiting == "storage":
            reason = "Paused: storage is full. Watch or delete some lessons to make room."
        running = self.conv.running_info()
        return {
            "enabled": self.enabled,
            "reason": reason,
            "count": self.count,
            "targets": ids,
            "ready": [i for i in ids if self.ready(i)],
            "converted": [v["id"] for v in videos if self.ready(v["id"])],
            "running": running,
            "queued": self.conv.queued_ids(),
            "spent": round(month_spend(), 4),
            "budget": self.budget,
        }
