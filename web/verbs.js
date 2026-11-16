// Verb drills — #/verbs (list) and #/verbs/<verb> (drill).
//
// verbs.py makes one short clip per Spanish sentence, per tense intro and per
// pronoun cue (verbs/<verb>.json says which). Pick tenses and pronouns, and the
// drill is assembled here into one WAV in memory and played in a normal <audio>
// element, so pause, the lock screen and speed all work like a lesson:
//
//   "Present tense: I have a dog." · Yo tengo un perro. · "You form." · Tú tienes un perro. · ...

const V = {
  el: (id) => document.getElementById(id),
  audio: null,
  data: null,        // verbs/<verb>.json of the open verb
  cues: [],          // [{start, end, es, en}] of the assembled drill
  builtFor: "",      // selection the current audio was built from
  url: null,
  buffers: new Map(),
  ctx: null,
  token: 0,
};
const PAUSE_AFTER_CUE = 0.6;  // English cue -> its Spanish (s)
const PAUSE_AFTER_ES = 1.8;   // after each Spanish sentence: time to repeat it (s)
const PAUSE_TENSE = 1.0;      // extra pause before the next tense (s)

function verbSel() {
  let s = {};
  try { s = JSON.parse(localStorage.getItem("verbSel")) || {}; } catch {}
  return { tenses: s.tenses || ["present"], pronouns: s.pronouns || ["yo", "tu", "el", "ella", "nosotros", "ellos", "ellas"],
           english: s.english !== false };
}
function saveVerbSel(s) { try { localStorage.setItem("verbSel", JSON.stringify(s)); } catch {} }

async function showVerbs(verb) {
  const token = ++V.token;
  V.el("verbs-list").hidden = !!verb;
  V.el("verb-drill").hidden = !verb;
  V.el("verbs-error").hidden = true;
  if (!verb) {
    V.el("verbs-heading").textContent = "Verbs";
    V.el("verbs-sub").textContent = "Pick a verb to drill";
    try {
      const list = await (await fetch("verbs/index.json", { cache: "no-cache" })).json();
      if (token !== V.token) return;
      const ul = V.el("verbs-list");
      ul.innerHTML = "";
      for (const v of list) {
        const li = document.createElement("li");
        li.innerHTML = `<a href="#/verbs/${encodeURIComponent(v.verb)}"><span class="t"></span><span class="d"></span></a>`;
        li.querySelector(".t").textContent = v.verb;
        li.querySelector(".d").textContent = v.meaning;
        ul.append(li);
      }
    } catch { verbsError("Couldn't load the verbs. Are you online?"); }
    return;
  }
  V.el("verbs-heading").textContent = verb;
  V.el("verbs-sub").textContent = "";
  if (!V.data || V.data.verb !== verb) {
    resetVerbAudio();
    try { V.data = await (await fetch(`verbs/${encodeURIComponent(verb)}.json`, { cache: "no-cache" })).json(); }
    catch { if (token === V.token) verbsError("Couldn't load this verb. Are you online?"); return; }
    if (token !== V.token) return;
  }
  V.el("verbs-sub").textContent = V.data.meaning;
  renderVerbChoices();
}

function verbsError(msg) {
  V.el("verbs-error").textContent = msg;
  V.el("verbs-error").hidden = false;
}

function renderVerbChoices() {
  const sel = verbSel();
  const chips = (box, items, key) => {
    box.innerHTML = "";
    for (const it of items) {
      const label = document.createElement("label");
      label.className = "chip-check";
      label.innerHTML = `<input type="checkbox"><span></span>`;
      label.querySelector("span").textContent = it.label;
      const input = label.querySelector("input");
      input.checked = sel[key].includes(it.id);
      input.onchange = () => {
        const s = verbSel();
        const on = new Set(s[key]);
        input.checked ? on.add(it.id) : on.delete(it.id);
        // Drill order; keep choices this verb doesn't show (haber has no preterite).
        const all = key === "tenses" ? ["present", "preterite", "imperfect", "imperative"] : items.map((x) => x.id);
        s[key] = all.filter((id) => on.has(id));
        saveVerbSel(s);
        selectionChanged();
      };
      box.append(label);
    }
  };
  chips(V.el("verb-tenses"), V.data.tenses.map((t) => ({ id: t.id, label: t.label })), "tenses");
  chips(V.el("verb-pronouns"), V.data.pronouns.map((p) => ({ id: p.id, label: p.es })), "pronouns");
  selectionChanged();
}

function selectionKey() {
  const s = verbSel();
  return `${V.data.verb}|${s.tenses}|${s.pronouns}|${s.english}`;
}

// The drill: [{clip, pause, es, en}] in play order. With English off only the
// Spanish is spoken; the English (intro / "She form.") still shows on screen.
function drillSteps() {
  const s = verbSel();
  const steps = [];
  for (const t of V.data.tenses.filter((t) => s.tenses.includes(t.id))) {
    if (steps.length) steps[steps.length - 1].pause += PAUSE_TENSE;
    if (s.english) steps.push({ clip: t.en_clip, pause: PAUSE_AFTER_CUE, en: t.en, es: "" });
    // Commands only have tú / usted / nosotros / nosotras / ustedes forms.
    V.data.pronouns.filter((p) => s.pronouns.includes(p.id) && t.forms[p.id]).forEach((p, i) => {
      const f = t.forms[p.id];
      // The intro already is the yo sentence in English: no "I form." right after it.
      const en = i === 0 ? t.en : p.cue;
      if (s.english && !(i === 0 && p.id === "yo")) steps.push({ clip: p.cue_clip, pause: PAUSE_AFTER_CUE, en: p.cue, es: "" });
      steps.push({ clip: f.clip, pause: PAUSE_AFTER_ES, en: s.english ? steps[steps.length - 1].en : en, es: f.es });
    });
  }
  return steps;
}

function selectionChanged() {
  // Count what will actually play: haber has no preterite, commands only have
  // tú / usted / nosotros / nosotras / ustedes.
  const n = drillSteps().filter((x) => x.es).length;
  V.el("verb-play").disabled = !n;
  V.el("verb-hint").textContent = n ? `${n} sentence${n > 1 ? "s" : ""}`
    : "Nothing to play. Pick a tense, and a pronoun it has (commands: Tú, Usted, Nosotros, Nosotras, Ustedes).";
  if (V.builtFor && V.builtFor !== selectionKey()) resetVerbAudio();
}

function resetVerbAudio() {
  const a = V.audio;
  if (a) { a.pause(); a.removeAttribute("src"); a.load(); }
  if (V.url) URL.revokeObjectURL(V.url);
  V.url = null; V.builtFor = ""; V.cues = [];
  V.el("verb-play").classList.remove("playing");
  V.el("verb-es").textContent = " ";
  V.el("verb-en").textContent = " ";
  V.el("verb-time").textContent = "";
}

async function decodeClip(path) {
  if (V.buffers.has(path)) return V.buffers.get(path);
  const bytes = await (await fetch(`verbs/${path}`)).arrayBuffer();
  const buf = await V.ctx.decodeAudioData(bytes);
  V.buffers.set(path, buf);
  return buf;
}

// Join the clips and pauses into one mono 16-bit WAV.
async function buildDrill() {
  V.ctx = V.ctx || new (window.AudioContext || window.webkitAudioContext)();
  const steps = drillSteps();
  const bufs = await Promise.all(steps.map((s) => decodeClip(s.clip)));
  const sr = V.ctx.sampleRate;
  let n = 0;
  for (let i = 0; i < steps.length; i++) n += bufs[i].length + Math.round(steps[i].pause * sr);
  const pcm = new Int16Array(n);
  const cues = [];
  let at = 0;
  steps.forEach((s, i) => {
    const ch = bufs[i].getChannelData(0);
    for (let j = 0; j < ch.length; j++) pcm[at + j] = Math.max(-1, Math.min(1, ch[j])) * 32767;
    const end = at + ch.length + Math.round(s.pause * sr);
    cues.push({ start: at / sr, end: end / sr, es: s.es, en: s.en });
    at = end;
  });
  const head = new DataView(new ArrayBuffer(44));
  const str = (o, t) => [...t].forEach((c, i) => head.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); head.setUint32(4, 36 + pcm.byteLength, true); str(8, "WAVE");
  str(12, "fmt "); head.setUint32(16, 16, true); head.setUint16(20, 1, true); head.setUint16(22, 1, true);
  head.setUint32(24, sr, true); head.setUint32(28, sr * 2, true); head.setUint16(32, 2, true); head.setUint16(34, 16, true);
  str(36, "data"); head.setUint32(40, pcm.byteLength, true);
  return { blob: new Blob([head, pcm], { type: "audio/wav" }), cues };
}

async function verbPlay() {
  const a = V.audio;
  const key = selectionKey();
  if (V.builtFor === key && a.src) { a.paused ? a.play() : a.pause(); return; }
  const btn = V.el("verb-play");
  btn.disabled = true;
  V.el("verb-hint").textContent = "Preparing…";
  try {
    const { blob, cues } = await buildDrill();
    if (selectionKey() !== key) return;  // changed while loading
    resetVerbAudio();
    V.url = URL.createObjectURL(blob);
    V.cues = cues;
    V.builtFor = key;
    a.src = V.url;
    a.playbackRate = Number(localStorage.getItem("speed")) || 1;
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({ title: `${V.data.verb} · ${V.data.meaning}`, artist: "Verbs" });
    }
    await a.play();
  } catch (e) {
    verbsError("Couldn't prepare the audio. Are you online?");
  } finally {
    btn.disabled = false;
    selectionChanged();
  }
}

function verbTick() {
  const t = V.audio.currentTime;
  const c = V.cues.find((c) => t >= c.start && t < c.end) || V.cues[V.cues.length - 1];
  if (!c) return;
  V.el("verb-es").textContent = c.es || " ";
  V.el("verb-en").textContent = c.en || " ";
  const fmtT = (x) => `${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, "0")}`;
  V.el("verb-time").textContent = `${fmtT(t)} / ${fmtT(V.audio.duration || 0)}`;
}

function initVerbs() {
  V.audio = V.el("verb-audio");
  V.el("verb-play").onclick = verbPlay;
  V.audio.addEventListener("timeupdate", verbTick);
  V.audio.addEventListener("play", () => V.el("verb-play").classList.add("playing"));
  V.audio.addEventListener("pause", () => V.el("verb-play").classList.remove("playing"));
  // Say English: off = Spanish sentences only
  const eng = V.el("verb-english");
  eng.checked = verbSel().english;
  eng.onchange = () => { const s = verbSel(); s.english = eng.checked; saveVerbSel(s); if (V.data) selectionChanged(); };
  // Repeat: loop the drill until it's turned off. Remembered between visits.
  const rep = V.el("verb-repeat");
  const setRepeat = (on) => {
    V.audio.loop = on;
    rep.setAttribute("aria-pressed", on);
    try { localStorage.setItem("verbRepeat", on ? "1" : ""); } catch {}
  };
  let saved = false;
  try { saved = localStorage.getItem("verbRepeat") === "1"; } catch {}
  setRepeat(saved);
  rep.onclick = () => setRepeat(!V.audio.loop);
  V.el("verb-restart").onclick = () => { if (V.audio.src) { V.audio.currentTime = 0; V.audio.play(); } };
}

function leaveVerbs() {
  if (V.audio) V.audio.pause();
  V.token++;
}
