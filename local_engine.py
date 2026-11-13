"""Free, fully local versions of the paid pipeline steps (runs on the Pi's CPU).

  transcribe  faster-whisper "small" (int8)        ~1.25x the audio length on a Pi 4
  translate   OPUS-MT es->en (Argos package) via CTranslate2
  tts         Piper, voice en_US-lessac-medium

Models live in models/ (not in git). To fetch them:
  - faster-whisper downloads "small" automatically on first use
  - OPUS-MT: unzip https://argos-net.com/v1/translate-es_en-1_9.argosmodel into models/argos_es_en/
  - Piper:   en_US-lessac-medium.onnx + .onnx.json from
             https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium into models/piper/

Same inputs and outputs as the cloud functions in make_audio.py, so the rest of
the pipeline (sentence grouping, splicing, publishing) is shared.
"""

import hashlib
import re
import subprocess
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
WHISPER_MODEL = "small"
TRANSLATE_DIR = MODELS / "argos_es_en" / "translate-es_en-1_9"
PIPER_VOICE = MODELS / "piper" / "en_US-lessac-medium.onnx"
THREADS = 4


def _pcm16k(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def transcribe(master, progress):
    """Returns {"words": [...], "segments": [...]} like the Whisper API version."""
    from faster_whisper import WhisperModel

    progress("Loading speech model")
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=THREADS)
    audio = _pcm16k(master)
    total = len(audio) / 16000
    segs, _ = model.transcribe(audio, language="es", word_timestamps=True, beam_size=1,
                               condition_on_previous_text=False)
    words, segments = [], []
    progress("Transcribing the Spanish", 0, 100)
    for s in segs:  # generator: decoding happens as we iterate
        segments.append({"text": s.text, "start": s.start, "end": s.end})
        for w in s.words or []:
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
        progress("Transcribing the Spanish", min(99, int(100 * s.end / total)), 100)
    return {"words": words, "segments": segments}


class _Translator:
    def __init__(self):
        import ctranslate2
        from sacremoses import MosesDetokenizer, MosesPunctNormalizer, MosesTokenizer
        from subword_nmt.apply_bpe import BPE

        self.tr = ctranslate2.Translator(str(TRANSLATE_DIR / "model"), device="cpu", compute_type="int8",
                                         inter_threads=1, intra_threads=THREADS)
        self.norm, self.tok = MosesPunctNormalizer(lang="es"), MosesTokenizer(lang="es")
        self.detok = MosesDetokenizer(lang="en")
        with open(TRANSLATE_DIR / "bpe.model", encoding="utf-8") as f:
            self.bpe = BPE(f)

    def encode(self, s):
        return self.bpe.segment_tokens(self.tok.tokenize(self.norm.normalize(s), escape=False))

    def decode(self, toks):
        return self.detok.detokenize(" ".join(toks).replace("@@ ", "").split())

    def translate(self, texts):
        res = self.tr.translate_batch([self.encode(t) for t in texts], beam_size=4, max_batch_size=16)
        return [self.decode(r.hypotheses[0]) for r in res]


def _split_sentences(text):
    # OPUS-MT tends to drop one of several sentences given together, so translate them one at a time.
    parts = re.split(r"(?<=[.?!…])\s+", text.strip())
    return [p for p in parts if p]


def translate(sents, progress):
    """Fills s["en"] for each sentence dict."""
    progress("Loading translation model")
    t = _Translator()
    batch = 20
    for i in range(0, len(sents), batch):
        chunk = sents[i:i + batch]
        pieces = [_split_sentences(s["es"]) or [s["es"]] for s in chunk]
        flat = t.translate([p for ps in pieces for p in ps])
        k = 0
        for s, ps in zip(chunk, pieces):
            s["en"] = " ".join(flat[k:k + len(ps)])
            k += len(ps)
        progress("Translating to English", min(i + batch, len(sents)), len(sents))
    return sents


def tts(sents, out_dir, progress):
    """Writes one WAV per sentence (cached by text) and returns their paths."""
    from piper import PiperVoice, SynthesisConfig

    out_dir.mkdir(parents=True, exist_ok=True)
    voice = PiperVoice.load(str(PIPER_VOICE))
    cfg = SynthesisConfig(length_scale=1.1)  # a touch slower than default, for learners
    paths = []
    for n, s in enumerate(sents, 1):
        path = out_dir / f"{hashlib.sha1(s['en'].encode()).hexdigest()[:16]}.wav"
        if not path.exists():
            tmp = path.with_suffix(".tmp.wav")
            with wave.open(str(tmp), "wb") as w:
                voice.synthesize_wav(s["en"], w, syn_config=cfg)
            tmp.rename(path)
        paths.append(path)
        progress("Generating English audio", n, len(sents))
    return paths
