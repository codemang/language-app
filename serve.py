#!/usr/bin/env python3
"""Serve the web app (web/) on the local network, with HTTP Range support
so the browser can seek inside the MP3s, plus a tiny conversion API:

  GET  /api/status?id=<video id>   -> {"state": "ready" | "running" | "queued" | "error" | "none", ...}
  POST /api/convert?id=<video id>&engine=local|kokoro|cloud
                                   -> starts converting that video (once), returns status.
                                      engine is required: nothing is converted (or paid
                                      for) unless the app explicitly asks for an engine.
  GET  /api/storage                -> {"used": bytes, "cap": bytes}
  GET  /api/auto                   -> auto-convert status (autopilot.py)
  POST /api/auto?enabled=0|1       -> switch auto-convert on/off (off also cancels its running job)
  GET  /api/progress               -> {video id: {t, mode, frac, watched, updated}} (progress.py)
  POST /api/progress?id=<video id> -> body {t, mode, frac, watched, updated}; returns the merged entry

Conversions run make_audio.py one at a time in the background. Finished lessons
live in web/lessons/<video id>/; lesson.json there means "ready". Audio is kept
under a size cap (storage.py): before each conversion, the least-recently-played
lessons are deleted if needed to make room.

  python serve.py [--port 8000]
"""

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

import autopilot
import progress
import storage

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
LESSONS = WEB / "lessons"
JOBS = ROOT / "work" / "jobs"


# ---------------------------------------------------------------- conversion jobs

# What each engine choice in the app means for make_audio.py
ENGINE_ARGS = {
    "local": ["--engine", "local", "--tts", "piper"],    # on the Pi, robotic voice, quick
    "kokoro": ["--engine", "local", "--tts", "kokoro"],  # on the Pi, natural voice, slower
    "cloud": ["--engine", "cloud"],                      # paid APIs, fastest
}

class Converter:
    """Runs make_audio.py one video at a time. Jobs the user starts from the app
    ("ui") always run before background ones ("auto", see autopilot.py)."""

    PRIORITY = {"ui": 0, "auto": 1}

    def __init__(self):
        self.cv = threading.Condition()
        self.jobs = {}          # video id -> {"engine", "source"} for queued + running jobs
        self.waiting = []       # queued video ids
        self.running = None
        self.proc = None
        self.cancelled = set()
        self.on_finish = None   # callback(video_id, source, ok, log_text), set by the autopilot
        threading.Thread(target=self._worker, daemon=True).start()

    @staticmethod
    def video(video_id):
        """Only videos from the app's own list can be converted."""
        try:
            videos = json.loads((WEB / "data" / "videos.json").read_text())
        except (OSError, ValueError):
            return None
        return next((v for v in videos if v["id"] == video_id), None)

    # ---- queries

    def status(self, video_id):
        if (LESSONS / video_id / "lesson.json").exists():
            return {"state": "ready"}
        with self.cv:
            job = self.jobs.get(video_id)
            if job and video_id == self.running:
                try:
                    st = json.loads((JOBS / f"{video_id}.json").read_text())
                except (OSError, ValueError):
                    st = {"message": "Starting"}
                return {**st, "state": "running", "engine": job["engine"], "source": job["source"]}
            if job:
                if self.running is None:  # about to be picked up by the worker
                    return {"state": "running", "message": "Starting", "engine": job["engine"], "source": job["source"]}
                return {"state": "queued", "message": "Waiting for another video to finish converting",
                        "engine": job["engine"], "source": job["source"]}
        try:
            st = json.loads((JOBS / f"{video_id}.json").read_text())
            if st.get("state") == "error":
                return st
        except (OSError, ValueError):
            pass
        return {"state": "none"}

    def is_busy(self, video_id):
        with self.cv:
            return video_id in self.jobs

    def has_auto_job(self):
        with self.cv:
            return any(j["source"] == "auto" for j in self.jobs.values())

    def queued_ids(self):
        with self.cv:
            return list(self.waiting)

    def running_info(self):
        with self.cv:
            vid = self.running
            job = self.jobs.get(vid) if vid else None
        if not job:
            return None
        st = self.status(vid)
        return {"id": vid, "source": job["source"], "message": st.get("message"),
                "done": st.get("done"), "total": st.get("total")}

    # ---- control

    def start(self, video_id, engine, source="ui"):
        if engine not in ENGINE_ARGS:
            return {"state": "error", "message": "Choose how to convert this video"}
        if not self.video(video_id):
            return {"state": "error", "message": "Unknown video"}
        st = self.status(video_id)
        if st["state"] in ("ready", "running", "queued"):
            with self.cv:  # the user asking for a queued background job moves it to the front
                job = self.jobs.get(video_id)
                if source == "ui" and job and job["source"] == "auto" and video_id != self.running:
                    job["source"] = "ui"
                    self._sort()
            return self.status(video_id)
        with self.cv:
            self.jobs[video_id] = {"engine": engine, "source": source, "seq": time.time()}
            self.waiting.append(video_id)
            self._sort()
            self.cv.notify()
        return self.status(video_id)

    def _sort(self):
        self.waiting.sort(key=lambda i: (self.PRIORITY[self.jobs[i]["source"]], self.jobs[i]["seq"]))

    def cancel_auto(self):
        """Stop background work: drop queued auto jobs and kill the running one."""
        with self.cv:
            for vid in [v for v in self.waiting if self.jobs[v]["source"] == "auto"]:
                self.waiting.remove(vid)
                self.jobs.pop(vid, None)
            if self.running and self.jobs.get(self.running, {}).get("source") == "auto" and self.proc:
                self.cancelled.add(self.running)
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    # ---- worker

    def _worker(self):
        while True:
            with self.cv:
                while not self.waiting:
                    self.cv.wait()
                video_id = self.waiting.pop(0)
                self.running = video_id
                job = dict(self.jobs[video_id])
            ok, log_text = False, ""
            try:
                ok, log_text = self._run(video_id, job)
            except Exception as e:
                log_text = repr(e)
            finally:
                with self.cv:
                    self.running = None
                    self.proc = None
                    self.jobs.pop(video_id, None)
                    self.cancelled.discard(video_id)
            if self.on_finish:
                self.on_finish(video_id, job["source"], ok, log_text)

    def _run(self, video_id, job):
        v = self.video(video_id)
        if not v:
            return False, "video no longer listed"
        auto = job["source"] == "auto"
        JOBS.mkdir(parents=True, exist_ok=True)
        status_file, log_file = JOBS / f"{video_id}.json", JOBS / f"{video_id}.log"
        status_file.write_text(json.dumps({"state": "running", "message": "Starting"}))
        watched = {k for k, e in progress.load().items() if e.get("watched")}
        targets = {t["id"] for t in AUTOPILOT.targets()} if AUTOPILOT else set()
        if not auto:  # the autopilot already made room for its own jobs
            storage.make_room(storage.needed_bytes(v.get("duration") or 0), protect={video_id},
                              watched=watched, keep_last=targets)
        args = [sys.executable, str(ROOT / "make_audio.py"), v.get("source") or v.get("youtube"),
                "--lesson-id", video_id, "--status-file", str(status_file), *ENGINE_ARGS[job["engine"]]]
        if auto:
            args.append("--bulk")  # 3-minute gap between stream downloads
        with open(log_file, "w") as log:
            # Own process group, so cancelling also stops its ffmpeg/whisper children.
            proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT, start_new_session=True,
                                    preexec_fn=(lambda: os.nice(10)) if auto else None)
            with self.cv:
                self.proc = proc
            rc = proc.wait()
        storage.make_room(0, protect={video_id} | targets, watched=watched, keep_last=targets,
                          only_watched=auto)  # in case the estimate was low
        log_text = log_file.read_text(errors="replace")
        if video_id in self.cancelled:
            status_file.unlink(missing_ok=True)
            return False, "cancelled"
        if rc == 0 and (LESSONS / video_id / "lesson.json").exists():
            status_file.unlink(missing_ok=True)
            return True, log_text
        lines = [l for l in log_text.splitlines() if l.strip()]
        status_file.write_text(json.dumps({
            "state": "error",
            "message": "Conversion failed",
            "detail": lines[-1][-300:] if lines else f"exit code {rc}",
        }))
        return False, log_text


AUTOPILOT = None  # set in main()
CONVERTER = Converter()


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".webmanifest": "application/manifest+json", ".mp3": "audio/mpeg",
                      ".js": "text/javascript"}

    # ---- API

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api(self, method):
        url = urlparse(self.path)
        if url.path == "/api/storage" and method == "GET":
            return self._json(storage.status())
        if url.path == "/api/progress" and method == "GET":
            return self._json(progress.load())
        if url.path == "/api/auto" and method == "GET":
            return self._json(AUTOPILOT.status())
        if url.path == "/api/auto" and method == "POST":
            on = (parse_qs(url.query).get("enabled") or [""])[0]
            if on not in ("0", "1"):
                return self._json({"error": "enabled=0|1"}, 400)
            return self._json(AUTOPILOT.set_enabled(on == "1"))
        q = parse_qs(url.query)
        video_id = (q.get("id") or [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
            return self._json({"state": "error", "message": "Bad id"}, 400)
        if url.path == "/api/status" and method == "GET":
            return self._json(CONVERTER.status(video_id))
        if url.path == "/api/convert" and method == "POST":
            return self._json(CONVERTER.start(video_id, (q.get("engine") or [""])[0]))
        if url.path == "/api/progress" and method == "POST":
            if not CONVERTER.video(video_id):
                return self._json({"error": "Unknown video"}, 404)
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 4096:
                return self._json({"error": "Bad body"}, 400)
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                return self._json({"error": "Bad JSON"}, 400)
            before = progress.load().get(video_id, {}).get("watched")
            saved = progress.save(video_id, body if isinstance(body, dict) else {})
            if saved and saved["watched"] and not before:
                AUTOPILOT.wake()  # the next unwatched video becomes a target
            return self._json(saved) if saved else self._json({"error": "Bad progress"}, 400)
        return self._json({"error": "not found"}, 404)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api("GET")
        if m := re.fullmatch(r"/lessons/([A-Za-z0-9_-]+)/(es|mix)\.mp3", urlparse(self.path).path):
            storage.mark_played(m.group(1))
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._api("POST")
        self.send_error(405)

    # ---- static files with Range support

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_head(self):
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not m or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start, end = m.groups()
        if start == "":  # suffix range: last N bytes
            start, end = max(0, size - int(end)), size - 1
        else:
            start, end = int(start), min(int(end) if end else size - 1, size - 1)
        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)
        self._remaining = None

    def log_message(self, fmt, *args):
        pass


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main():
    global AUTOPILOT
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    AUTOPILOT = autopilot.Autopilot(CONVERTER)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), partial(Handler, directory=str(WEB)))
    print(f"Serving {WEB}")
    print(f"  on this machine:  http://localhost:{args.port}")
    print(f"  on your network:  http://{lan_ip()}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
