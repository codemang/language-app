#!/usr/bin/env python3
"""Change the pause after each Spanish/English pair in already-converted lessons.

Rebuilds each lesson's mix.mp3 from the mix itself: lesson.json says where every
sentence's Spanish and English sit, so each pair is cut out and the pairs are
joined again with the new pause. No conversion cache, API calls or Pi-heavy work
needed; es.mp3 is untouched. Safe to run again with a different value.

  python respace_lessons.py [--pair-gap 2.0] [--lessons-dir web/lessons] [lesson_id ...]

Copies already downloaded to a phone keep their old spacing until downloaded again.
"""

import argparse
import json
import subprocess
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SR = 24000
MP3 = ["-c:a", "libmp3lame", "-b:a", "96k"]


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def respace(folder, pair_gap):
    lj = folder / "lesson.json"
    lesson = json.loads(lj.read_text())
    sents = lesson["sentences"]
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "mix.wav", Path(tmp) / "new.wav"
        ffmpeg("-i", str(folder / "mix.mp3"), "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(src))
        with wave.open(str(src)) as w:
            pcm = w.readframes(w.getnframes())
        total = len(pcm) // 2
        silence = b"\0\0" * round(pair_gap * SR)
        parts, t = [], 0
        for s in sents:
            # one pair = its Spanish clip, the short ES->EN gap, and its English clip
            a = round(s["mix_es"][0] * SR)
            b = min(round(s["mix_en"][1] * SR), total)
            shift = t - a  # in samples: where this pair moves to
            parts += [pcm[a * 2:b * 2], silence]
            for key in ("mix_es", "mix_en"):
                s[key] = [round(x + shift / SR, 3) for x in s[key]]
            t += (b - a) + len(silence) // 2
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(b"".join(parts))
        ffmpeg("-i", str(out), *MP3, str(folder / "mix.tmp.mp3"))
    (folder / "mix.tmp.mp3").replace(folder / "mix.mp3")
    lesson["duration"]["mix"] = round(t / SR, 3)
    lesson["pair_gap"] = pair_gap
    tmp_json = lj.with_suffix(".tmp")
    tmp_json.write_text(json.dumps(lesson, ensure_ascii=False, indent=2))
    tmp_json.replace(lj)
    return lesson["duration"]["mix"]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pair-gap", type=float, default=2.0, help="pause after each pair, seconds")
    p.add_argument("--lessons-dir", type=Path, default=ROOT / "web" / "lessons")
    p.add_argument("ids", nargs="*", help="only these lessons (default: all)")
    args = p.parse_args()
    folders = [args.lessons_dir / i for i in args.ids] if args.ids else sorted(
        d for d in args.lessons_dir.iterdir() if (d / "lesson.json").exists())
    for d in folders:
        before = json.loads((d / "lesson.json").read_text())["duration"]["mix"]
        after = respace(d, args.pair_gap)
        print(f"{d.name}: mix {before:.0f}s -> {after:.0f}s")


if __name__ == "__main__":
    main()
