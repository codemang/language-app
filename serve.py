#!/usr/bin/env python3
"""Serve the web app (web/) on the local network, with HTTP Range support
so the browser can seek inside the MP3s, plus a tiny conversion API:

  GET  /api/status?id=<video id>   -> {"state": "ready" | "running" | "queued" | "error" | "none", ...}
  POST /api/convert?id=<video id>&engine=local|kokoro|cloud
                                   -> starts converting that video (once), returns status.
                                      engine is required: nothing is converted (or paid
                                      for) unless the app explicitly asks for an engine.
  GET  /api/storage                -> {"used": bytes, "cap": bytes}
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
import queue
import re
import socket
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

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
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = set()  # queued or running video ids
        self.running = None
        self.engines = {}  # video id -> engine it was queued with
        self.q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    @staticmethod
    def video(video_id):
        """Only videos from the app's own list can be converted."""
        try:
            videos = json.loads((WEB / "data" / "videos.json").read_text())
        except (OSError, ValueError):
            return None
        return next((v for v in videos if v["id"] == video_id), None)

    def status(self, video_id):
        if (LESSONS / video_id / "lesson.json").exists():
            return {"state": "ready"}
        with self.lock:
            if video_id == self.running:
                engine = self.engines.get(video_id)
                try:
                    st = json.loads((JOBS / f"{video_id}.json").read_text())
                except (OSError, ValueError):
                    st = {"message": "Starting"}
                return {**st, "state": "running", "engine": engine}
            if video_id in self.pending:
                engine = self.engines.get(video_id)
                if self.running is None:  # about to be picked up by the worker
                    return {"state": "running", "message": "Starting", "engine": engine}
                return {"state": "queued", "message": "Waiting for another video to finish converting",
                        "engine": engine}
        try:
            st = json.loads((JOBS / f"{video_id}.json").read_text())
            if st.get("state") == "error":
                return st
        except (OSError, ValueError):
            pass
        return {"state": "none"}

    def start(self, video_id, engine):
        if engine not in ENGINE_ARGS:
            return {"state": "error", "message": "Choose how to convert this video"}
        if not self.video(video_id):
            return {"state": "error", "message": "Unknown video"}
        st = self.status(video_id)
        if st["state"] in ("ready", "running", "queued"):
            return st
        with self.lock:
            self.pending.add(video_id)
            self.engines[video_id] = engine
        self.q.put(video_id)
        return self.status(video_id)

    def _worker(self):
        while True:
            video_id = self.q.get()
            with self.lock:
                self.running = video_id
            try:
                self._run(video_id)
            finally:
                with self.lock:
                    self.running = None
                    self.pending.discard(video_id)
                    self.engines.pop(video_id, None)

    def _run(self, video_id):
        v = self.video(video_id)
        engine = self.engines.get(video_id, "local")
        JOBS.mkdir(parents=True, exist_ok=True)
        status_file, log_file = JOBS / f"{video_id}.json", JOBS / f"{video_id}.log"
        status_file.write_text(json.dumps({"state": "running", "message": "Starting"}))
        storage.make_room(storage.needed_bytes(v.get("duration") or 0), protect={video_id})
        with open(log_file, "w") as log:
            rc = subprocess.run(
                [sys.executable, str(ROOT / "make_audio.py"), v.get("source") or v.get("youtube"),
                 "--lesson-id", video_id, "--status-file", str(status_file), *ENGINE_ARGS[engine]],
                stdout=log, stderr=subprocess.STDOUT, cwd=ROOT,
            ).returncode
        storage.make_room(0, protect={video_id})  # in case the estimate was low
        if rc == 0 and (LESSONS / video_id / "lesson.json").exists():
            status_file.unlink(missing_ok=True)
        else:
            lines = [l for l in log_file.read_text(errors="replace").splitlines() if l.strip()]
            status_file.write_text(json.dumps({
                "state": "error",
                "message": "Conversion failed",
                "detail": lines[-1][-300:] if lines else f"exit code {rc}",
            }))


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
            saved = progress.save(video_id, body if isinstance(body, dict) else {})
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
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
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
