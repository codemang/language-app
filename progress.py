"""Listening progress per video, saved on the Pi so every device sees the same state.

state/progress.json: {video_id: {"t": seconds, "mode": "es"|"mix", "frac": 0-1,
                                 "watched": bool, "updated": epoch ms}}

Merge rule (devices can be offline for a while): the entry with the newer
"updated" wins, but "watched" is sticky -- once true it stays true.
"""

import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILE = ROOT / "state" / "progress.json"
_lock = threading.Lock()


def load():
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {}


def _clean(entry):
    """Keep only known fields, with sane types (input comes from the browser)."""
    out = {}
    try:
        out["t"] = max(0.0, float(entry.get("t") or 0))
        out["frac"] = min(1.0, max(0.0, float(entry.get("frac") or 0)))
        out["updated"] = int(entry.get("updated") or 0)
    except (TypeError, ValueError):
        return None
    out["mode"] = "mix" if entry.get("mode") == "mix" else "es"
    out["watched"] = bool(entry.get("watched"))
    return out


def save(video_id, entry):
    """Merge one device's entry into the file; returns the stored entry."""
    new = _clean(entry)
    if new is None:
        return None
    with _lock:
        data = load()
        old = data.get(video_id)
        if old and old.get("updated", 0) > new["updated"]:
            merged = {**old, "watched": old.get("watched") or new["watched"]}
        else:
            merged = {**new, "watched": new["watched"] or bool(old and old.get("watched"))}
        data[video_id] = merged
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(FILE)
        return merged
