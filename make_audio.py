#!/usr/bin/env python3
"""Turn a Spanish YouTube video into an interleaved Spanish/English audio lesson.

Pipeline:
  1. Download audio with yt-dlp
  2. Transcribe with OpenAI Whisper API (word + segment timestamps)
  3. Group words into sentences (merge tiny ones, split very long ones)
  4. Translate each sentence to English with Claude
  5. Generate English speech with OpenAI TTS
  6. Splice: ES sentence, EN sentence, ES sentence, ... -> one MP3

Every step caches its output under work/<video_id>/, so re-running is cheap.

Usage:
  python make_audio.py <youtube_url | dreaming:<video id>> [--minutes 2]

For dreaming:<id>, audio comes from Dreaming Spanish's own stream and the Spanish
text from its human-made subtitles (Whisper is then only used for word timings).
Needs DREAMING_TOKEN in .env.

Output: web/lessons/<id>/{es.mp3, mix.mp3, lesson.json} for the web app.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import anthropic
import yt_dlp
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
SR = 24000  # sample rate for all spliced audio

# Sentence-shaping knobs
MIN_SENTENCE_SEC = 1.5
MIN_SENTENCE_WORDS = 3
MAX_SENTENCE_SEC = 15.0
PAUSE_BREAK_SEC = 1.5      # a silence this long ends a sentence even without punctuation
SEGMENT_BREAK_SEC = 0.5    # ...or this long, if Whisper also ended a segment there
MERGE_MAX_GAP_SEC = 1.0    # short fragments only merge with a neighbour this close
COMMA_BREAK_SEC = 3.0      # after a comma the sentence continues, unless the pause is this long
PAD_BEFORE = 0.15
PAD_AFTER = 0.30

WHISPER_MAX_BYTES = 24 * 1024 * 1024
ASR_CHUNK_SEC = 20 * 60

CLAUDE_MODEL = "claude-opus-5-5"
TRANSLATORS = {"opus": "claude-opus-5-5", "haiku": "claude-haiku-5-5"}  # "local" = OPUS-MT, see local_engine.py
TRANSLATE_BATCH = 60
TRANSLATE_CONTEXT = 5


def log(msg):
    print(msg, flush=True)


STATUS_FILE = None  # set by --status-file; the web server polls it to show progress


def progress(message, done=None, total=None):
    """Report the current step (for the app's loading screen)."""
    log(message if total is None else f"{message} {done}/{total}")
    if STATUS_FILE:
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"state": "running", "message": message, "done": done, "total": total}))
        tmp.replace(STATUS_FILE)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out.strip())


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- 1. download

def video_id(url):
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return info["id"], info.get("title", "")


DREAMING_PREFIX = "dreaming:"


def dreaming_id(url):
    """'dreaming:<id>' or an app.dreaming.com link -> Dreaming Spanish video id, else None."""
    if url.startswith(DREAMING_PREFIX):
        return url[len(DREAMING_PREFIX):]
    if "dreaming.com" in url and (m := re.search(r"\b([0-9a-f]{24})\b", url)):
        return m.group(1)
    return None


def download_dreaming(v, work):
    """Audio from Dreaming Spanish's own stream, plus its human-made Spanish subtitles."""
    import dreaming
    save_json(work / "subtitles.json", dreaming.subtitle_cues(v))
    existing = list(work.glob("source.*"))
    if existing:
        return existing[0]
    progress("Downloading audio")
    tmp = work / "download.m4a"
    dreaming.download_audio(v, tmp)
    return tmp.rename(work / "source.m4a")


def download(url, work):
    existing = list(work.glob("source.*"))
    if existing:
        return existing[0]
    progress("Downloading audio")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(work / "source.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return next(work.glob("source.*"))


def make_master(source, work, minutes):
    """Decode source once to a mono WAV; optionally trimmed to the first N minutes."""
    master = work / f"master_{minutes or 'full'}.wav"
    if not master.exists():
        trim = ["-t", str(minutes * 60)] if minutes else []
        ffmpeg("-i", str(source), *trim, "-ac", "1", "-ar", str(SR), str(master))
    return master


# ---------------------------------------------------------------- 2. transcribe

def transcribe(master, work, minutes):
    cache = work / f"transcript_{minutes or 'full'}.json"
    if (t := load_json(cache)) is not None:
        return t
    progress("Transcribing the Spanish")
    client = OpenAI()
    total = duration(master)
    words, segments = [], []
    offset = 0.0
    while offset < total:
        chunk = work / "asr_chunk.mp3"
        # Mono 32 kbps MP3 is ~14 MB/hour, well under Whisper's 25 MB limit per chunk.
        ffmpeg("-ss", str(offset), "-t", str(ASR_CHUNK_SEC), "-i", str(master),
               "-ac", "1", "-ar", "16000", "-b:a", "32k", str(chunk))
        assert chunk.stat().st_size < WHISPER_MAX_BYTES
        with open(chunk, "rb") as f:
            r = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="es",
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
        for w in r.words or []:
            words.append({"word": w.word, "start": w.start + offset, "end": w.end + offset})
        for s in r.segments or []:
            segments.append({"text": s.text, "start": s.start + offset, "end": s.end + offset})
        chunk.unlink()
        offset += ASR_CHUNK_SEC
    t = {"words": words, "segments": segments}
    save_json(cache, t)
    return t


# ---------------------------------------------------------------- 3. sentences

def _norm(tok):
    return re.sub(r"[^\w]", "", tok.lower())


def timed_tokens(transcript):
    """Whisper's word list has timings but no punctuation; segment text has punctuation
    but only coarse timings. Align the two so every punctuated token gets a time."""
    tokens, seg_last = [], set()
    for seg in transcript["segments"]:
        tokens += seg["text"].split()
        if tokens:
            seg_last.add(len(tokens) - 1)
    words = transcript["words"]
    a = [_norm(t) for t in tokens]
    b = [_norm(w["word"]) for w in words]
    times = [None] * len(tokens)
    for blk in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            w = words[blk.b + k]
            times[blk.a + k] = (w["start"], w["end"])
    # Fill unmatched tokens from their neighbours.
    for i in range(len(times)):
        if times[i] is None:
            prev_end = times[i - 1][1] if i > 0 and times[i - 1] else None
            nxt = next((times[j] for j in range(i + 1, len(times)) if times[j]), None)
            start = prev_end if prev_end is not None else (nxt[0] if nxt else 0.0)
            end = nxt[0] if nxt else start
            times[i] = (start, max(start, end))
    return [{"text": t, "start": s, "end": e, "seg_end": i in seg_last}
            for i, (t, (s, e)) in enumerate(zip(tokens, times))]


def _sentence(toks):
    return {"es": " ".join(t["text"] for t in toks), "start": toks[0]["start"],
            "end": toks[-1]["end"], "tokens": toks}


def _split_long(toks):
    s = _sentence(toks)
    if s["end"] - s["start"] <= MAX_SENTENCE_SEC or len(toks) < 6:
        return [toks]
    mid = (s["start"] + s["end"]) / 2
    cand = range(2, len(toks) - 2)
    # Prefer a full stop inside the chunk, then a comma, then the longest pause.
    stops = [i for i in cand if re.search(r"[.?!…]\W*$", toks[i]["text"])]
    commas = [i for i in cand if toks[i]["text"].endswith((",", ";", ":"))]
    if stops or commas:
        cut = min(stops or commas, key=lambda i: abs(toks[i]["end"] - mid)) + 1
    else:
        cut = max(cand, key=lambda i: toks[i + 1]["start"] - toks[i]["end"]) + 1
    return _split_long(toks[:cut]) + _split_long(toks[cut:])


def sentences(transcript):
    toks = timed_tokens(transcript)
    groups, cur = [], []
    for i, t in enumerate(toks):
        cur.append(t)
        gap = toks[i + 1]["start"] - t["end"] if i + 1 < len(toks) else 0
        continues = t["text"].endswith((",", ";", ":"))
        if (re.search(r"[.?!…]['\"»”)]*$", t["text"])
                or gap >= (COMMA_BREAK_SEC if continues else PAUSE_BREAK_SEC)
                or (t["seg_end"] and not continues and gap >= SEGMENT_BREAK_SEC)):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    # Merge fragments that are too short to be useful on their own ("Sí." "Bueno."),
    # but only with a neighbour that follows closely -- never across a long silence.
    def short(g):
        return g[-1]["end"] - g[0]["start"] < MIN_SENTENCE_SEC or len(g) < MIN_SENTENCE_WORDS

    def gap(a, b):
        return b[0]["start"] - a[-1]["end"]

    merged = []
    for g in groups:  # forward: a short fragment joins the sentence after it
        if merged and short(merged[-1]) and gap(merged[-1], g) <= MERGE_MAX_GAP_SEC:
            merged[-1] = merged[-1] + g
        else:
            merged.append(g)
    i = 1
    while i < len(merged):  # backward: a short leftover joins the sentence before it
        if short(merged[i]) and gap(merged[i - 1], merged[i]) <= MERGE_MAX_GAP_SEC:
            merged[i - 1] = merged[i - 1] + merged.pop(i)
        else:
            i += 1

    out = []
    for g in merged:
        for piece in _split_long(g):
            s = _sentence(piece)
            del s["tokens"]
            out.append(s)
    for i, s in enumerate(out):
        s["id"] = i
    return out


# ---------------------------------------------------------------- 4. translate

TRANSLATE_SYSTEM = """You translate Spanish transcript lines into natural spoken English for a language-learning audio app.
Each line will be played as Spanish audio followed by your English, so:
- Translate the meaning faithfully and completely; keep the speaker's tone and register.
- Write English that sounds natural when read aloud. No notes, brackets or explanations.
- The lines come from automatic speech recognition and may be cut mid-sentence; translate each line on its own terms, using the surrounding lines only for context.
- Return exactly one translation per input id."""

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "en": {"type": "string"}},
                "required": ["id", "en"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


def _translate_batch(client, batch, context, model, usage):
    ctx = "\n".join(f"{s['es']}  =>  {s['en']}" for s in context)
    lines = "\n".join(f"{s['id']}: {s['es']}" for s in batch)
    prompt = (f"Previous lines, already translated (context only):\n{ctx}\n\n" if ctx else "") + \
             f"Translate these lines:\n{lines}"
    req = dict(
        model=model,
        max_tokens=16000,
        system=TRANSLATE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": TRANSLATE_SCHEMA}},
    )
    if model == "claude-opus-5-5":
        # Server-side fallback if Opus declines a request (not offered for Haiku)
        r = client.beta.messages.create(**req, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    else:
        r = client.messages.create(**req)
    usage[0] += r.usage.input_tokens
    usage[1] += r.usage.output_tokens
    if r.stop_reason != "end_turn":
        raise RuntimeError(f"Translation stopped early: {r.stop_reason} {getattr(r, 'stop_details', '')}")
    text = next(b.text for b in r.content if b.type == "text")
    return {t["id"]: t["en"] for t in json.loads(text)["translations"]}


def translate(sents, work, tag, model=CLAUDE_MODEL):
    cache = work / (f"sentences_{tag}.json" if model == CLAUDE_MODEL else f"sentences_{tag}_{model}.json")
    cached = load_json(cache)
    if cached and [s["es"] for s in cached] == [s["es"] for s in sents] and all(s.get("en") for s in cached):
        return cached
    progress("Translating to English", 0, len(sents))
    client = anthropic.Anthropic()
    usage = [0, 0]
    for i in range(0, len(sents), TRANSLATE_BATCH):
        batch = sents[i:i + TRANSLATE_BATCH]
        context = sents[max(0, i - TRANSLATE_CONTEXT):i]
        got = _translate_batch(client, batch, context, model, usage)
        missing = [s for s in batch if s["id"] not in got]
        if missing:  # one retry for anything the model skipped
            got.update(_translate_batch(client, missing, context, model, usage))
        for s in batch:
            s["en"] = got[s["id"]]
        progress("Translating to English", min(i + TRANSLATE_BATCH, len(sents)), len(sents))
    log(f"{model}: {usage[0]} input + {usage[1]} output tokens")
    save_json(cache, sents)
    return sents


# ---------------------------------------------------------------- 5. TTS

def tts(sents, work, voice):
    out_dir = work / f"en_{voice}"
    out_dir.mkdir(exist_ok=True)
    client = OpenAI()

    def path_for(s):  # keyed by text so edited translations get fresh audio
        return out_dir / f"{hashlib.sha1(s['en'].encode()).hexdigest()[:16]}.wav"

    todo = [s for s in sents if not path_for(s).exists()]
    if todo:
        progress("Generating English audio", len(sents) - len(todo), len(sents))
    done = [len(sents) - len(todo)]
    lock = threading.Lock()

    def one(s):
        path = path_for(s)
        if path.exists():
            return path
        tmp = path.with_suffix(".tmp.wav")
        with client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice=voice,
            input=s["en"],
            instructions="Speak clearly and naturally, at a calm, slightly slow pace for a language learner.",
            response_format="wav",
        ) as r:
            r.stream_to_file(tmp)
        tmp.rename(path)
        with lock:
            done[0] += 1
            progress("Generating English audio", done[0], len(sents))
        return path

    with ThreadPoolExecutor(4) as pool:
        return list(pool.map(one, sents))


# ---------------------------------------------------------------- 6. splice + publish

# CBR MP3: browsers seek CBR files accurately; VBR seeking can drift by seconds,
# which would break the ES <-> mix position mapping in the app.
MP3 = ["-c:a", "libmp3lame", "-b:a", "96k"]


def wav_seconds(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def splice(sents, master, en_paths, work, out, gap, pair_gap):
    """Build the mixed MP3 and return per-sentence timings in both the original
    (Spanish-only) audio and the mixed audio."""
    progress("Building the audio files")
    clips = work / "clips"
    clips.mkdir(exist_ok=True)
    total = duration(master)
    fmt = ["-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le"]

    def silence(sec):
        p = clips / f"silence_{sec:.2f}.wav"
        if not p.exists():
            ffmpeg("-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono", "-t", str(sec), *fmt, str(p))
        return p

    gap_wav, pair_wav = silence(gap), silence(pair_gap)
    gap_len, pair_len = wav_seconds(gap_wav), wav_seconds(pair_wav)
    parts, timeline, t = [], [], 0.0
    for i, s in enumerate(sents):
        nxt = sents[i + 1]["start"] if i + 1 < len(sents) else total
        start = max(0.0, s["start"] - PAD_BEFORE)
        end = min(nxt, s["end"] + PAD_AFTER, total)
        es = clips / f"es_{s['id']:04d}.wav"
        ffmpeg("-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(master), *fmt, str(es))
        en = clips / f"en_{s['id']:04d}.wav"
        ffmpeg("-i", str(en_paths[i]), *fmt, str(en))
        es_len, en_len = wav_seconds(es), wav_seconds(en)
        timeline.append({
            "id": s["id"], "es": s["es"], "en": s["en"],
            "orig": [round(start, 3), round(start + es_len, 3)],
            "mix_es": [round(t, 3), round(t + es_len, 3)],
            "mix_en": [round(t + es_len + gap_len, 3), round(t + es_len + gap_len + en_len, 3)],
        })
        t += es_len + gap_len + en_len + pair_len
        parts += [es, gap_wav, en, pair_wav]

    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing), *MP3, str(out))
    return timeline


def publish(vid, title, url, tag, master, sents, en_paths, work, args):
    """Write a lesson folder the web app can play: es.mp3, mix.mp3, lesson.json."""
    lesson_id = args.lesson_id or f"{vid}_{tag}"
    out = args.lessons_dir / lesson_id
    out.mkdir(parents=True, exist_ok=True)

    progress("Building the audio files")
    ffmpeg("-i", str(master), *MP3, str(out / "es.mp3"))
    timeline = splice(sents, master, en_paths, work, out / "mix.mp3", args.gap, args.pair_gap)

    (out / "transcript.txt").write_text(
        "".join(f"[{s['orig'][0]:7.1f}] {s['es']}\n          {s['en']}\n\n" for s in timeline))
    # Written last: the existence of lesson.json is what marks a lesson as ready.
    save_json(out / "lesson.json", {
        "id": lesson_id, "title": title, "url": url,
        "duration": {"es": round(duration(out / "es.mp3"), 3), "mix": round(duration(out / "mix.mp3"), 3)},
        "sentences": timeline,
    })

    # Library index the app loads on start
    index_path = args.lessons_dir / "index.json"
    index = [l for l in (load_json(index_path) or []) if l["id"] != lesson_id]
    index.insert(0, {"id": lesson_id, "title": title + (f" (first {tag})" if tag != "full" else "")})
    save_json(index_path, index)
    return out


# ---------------------------------------------------------------- main

def main():
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url")
    p.add_argument("--minutes", type=float, help="only process the first N minutes (cheap testing)")
    p.add_argument("--lessons-dir", type=Path, default=ROOT / "web" / "lessons",
                   help="where lesson folders are written (default: web/lessons)")
    p.add_argument("--lesson-id", help="folder name under --lessons-dir (default: <youtube id>_<length>)")
    p.add_argument("--status-file", type=Path, help="write progress JSON here (used by the web server)")
    p.add_argument("--engine", choices=["cloud", "local"], default="cloud",
                   help="cloud = OpenAI Whisper + Claude + OpenAI TTS (paid, fast); "
                        "local = faster-whisper + OPUS-MT + Piper on this machine (free, slower)")
    p.add_argument("--translator", choices=["opus", "haiku", "local"],
                   help="who translates to English (default: opus for --engine cloud, haiku for --engine local)")
    p.add_argument("--voice", default="coral", help="OpenAI TTS voice (alloy, ash, coral, echo, nova, sage, ...)")
    p.add_argument("--gap", type=float, default=0.5, help="silence between Spanish and English (s)")
    p.add_argument("--pair-gap", type=float, default=1.0, help="silence after each ES/EN pair (s)")
    args = p.parse_args()
    global STATUS_FILE
    STATUS_FILE = args.status_file

    if args.engine == "cloud" and not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (put it in .env)")

    ds_id = dreaming_id(args.url)
    if ds_id:
        import dreaming
        try:
            v = dreaming.video(ds_id)
        except dreaming.DreamingError as e:
            sys.exit(str(e))
        vid, title = ds_id, v["title"]
    else:
        vid, title = video_id(args.url)
    log(f"Video: {title} ({vid})")
    work = ROOT / "work" / vid
    work.mkdir(parents=True, exist_ok=True)
    tag = f"{args.minutes:g}m" if args.minutes else "full"

    source = download_dreaming(v, work) if ds_id else download(args.url, work)
    master = make_master(source, work, args.minutes)
    if args.engine == "local":
        import local_engine
        cache = work / f"transcript_{args.minutes or 'full'}_local.json"
        transcript = load_json(cache)
        if transcript is None:
            transcript = local_engine.transcribe(master, progress)
            save_json(cache, transcript)
    else:
        transcript = transcribe(master, work, args.minutes)
    cues = load_json(work / "subtitles.json") if ds_id else None
    if cues:
        # Human subtitles give the words and punctuation; Whisper gives the word timings.
        limit = args.minutes * 60 if args.minutes else float("inf")
        transcript = {"words": transcript["words"], "segments": [c for c in cues if c["start"] < limit]}
        log(f"Using {len(transcript['segments'])} subtitle lines for the Spanish text")
    sents = sentences(transcript)
    log(f"{len(sents)} sentences")
    if not sents:
        sys.exit("No speech found in this video")
    translator = args.translator or ("haiku" if args.engine == "local" else "opus")
    if translator == "local":
        cache = work / f"sentences_{tag}_local.json"
        cached = load_json(cache)
        if cached and [c["es"] for c in cached] == [s["es"] for s in sents] and all(c.get("en") for c in cached):
            sents = cached
        else:
            import local_engine
            sents = local_engine.translate(sents, progress)
            save_json(cache, sents)
    else:
        sents = translate(sents, work, tag, TRANSLATORS[translator])

    if args.engine == "local":
        en_paths = local_engine.tts(sents, work / "en_piper", progress)
    else:
        en_paths = tts(sents, work, args.voice)

    out = publish(vid, title, args.url, tag, master, sents, en_paths, work, args)
    log(f"Done: {out}")


if __name__ == "__main__":
    main()
