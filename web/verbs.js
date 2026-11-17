// Verb drills — #/verbs (list) and #/verbs/<verb> (drill) — and object-pronoun
// drills — #/pronouns — on the same screen.
//
// Pronouns (pronouns.py -> verbs/pronouns.json): pick items in three sets
// (direct, indirect, both); each plays its English sentence, then the Spanish.
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
  kind: "verb",      // "verb" | "pron"
  data: null,        // verbs/<verb>.json of the open verb, or verbs/pronouns.json
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
// Pronoun picks: {direct: [...], indirect: [...], both: [...]}. "Say the English"
// is shared with the verbs (it lives in verbSel).
function pronSel() {
  let s = {};
  try { s = JSON.parse(localStorage.getItem("pronSel")) || {}; } catch {}
  return { direct: s.direct || ["me", "te", "lo", "la", "nos", "los", "las"], indirect: s.indirect || [], both: s.both || [] };
}
function savePronSel(s) { try { localStorage.setItem("pronSel", JSON.stringify(s)); } catch {} }

async function showPronouns() {
  const token = ++V.token;
  V.el("verbs-list").hidden = true;
  V.el("verb-drill").hidden = false;
  V.el("verbs-error").hidden = true;
  V.el("verbs-dl").hidden = true;
  V.el("verbs-heading").textContent = "Pronouns";
  V.el("verbs-sub").textContent = "Direct and indirect object pronouns";
  if (V.kind !== "pron" || !V.data) {
    resetVerbAudio();
    V.kind = "pron"; V.data = null;
    try { V.data = await (await fetch("verbs/pronouns.json", { cache: "no-cache" })).json(); }
    catch { if (token === V.token) { V.el("verb-drill").hidden = true; verbsError("Couldn't load the pronouns. Are you online?"); } return; }
    if (token !== V.token) return;
  }
  const sel = pronSel();
  renderGroups(V.data.sets.map((set) => ({
    title: set.title, picked: sel[set.id], items: set.items.map((it) => ({ id: it.id, label: it.label })),
    toggle: (id, on) => {
      const s = pronSel();
      const picked = new Set(s[set.id]);
      on ? picked.add(id) : picked.delete(id);
      s[set.id] = set.items.map((x) => x.id).filter((x) => picked.has(x));
      savePronSel(s);
    },
  })));
}

// verbs/index.json: {verbs: [{verb, meaning}], collections: [{id, title, desc, verbs}]}
// (an older copy saved offline may still be the plain verb list)
function readIndex(idx) {
  return Array.isArray(idx) ? { verbs: idx, collections: [] } : idx;
}

// #/verbs: the collections · #/verbs/c/<id>: one collection (c/all: every verb)
// · #/verbs/<verb>: the drill
async function showVerbs(arg) {
  const token = ++V.token;
  const col = arg && arg.startsWith("c/") ? arg.slice(2) : null;
  const verb = col ? null : arg;
  V.el("verbs-list").hidden = !!verb;
  V.el("verb-drill").hidden = !verb;
  V.el("verbs-error").hidden = true;
  V.el("verbs-dl").hidden = !!arg;
  if (!verb) {
    if (!col) renderVerbsDl();
    V.el("verbs-back").href = col ? "#/verbs" : "#/";
    V.lastList = col ? `#/verbs/${arg}` : "#/verbs";
    V.el("verbs-heading").textContent = "Verbs";
    V.el("verbs-sub").textContent = col ? " " : "Pick a collection";
    try {
      const idx = readIndex(await (await fetch("verbs/index.json", { cache: "no-cache" })).json());
      if (token !== V.token) return;
      const ul = V.el("verbs-list");
      ul.innerHTML = "";
      const item = (href, title, detail) => {
        const li = document.createElement("li");
        li.innerHTML = `<a href="${href}"><span class="t"></span><span class="d"></span></a>`;
        li.querySelector(".t").textContent = title;
        li.querySelector(".d").textContent = detail;
        ul.append(li);
      };
      const count = (n) => `${n} verb${n === 1 ? "" : "s"}`;
      if (!col) {
        for (const c of idx.collections.filter((c) => c.verbs.length)) item(`#/verbs/c/${c.id}`, c.title, count(c.verbs.length));
        item("#/verbs/c/all", "All verbs", count(idx.verbs.length));
        renderVerbsDl(idx.verbs);
        return;
      }
      const c = col === "all" ? { title: "All verbs", desc: "Every verb in the app", verbs: idx.verbs.map((v) => v.verb) }
        : idx.collections.find((x) => x.id === col);
      if (!c) { verbsError("That collection doesn't exist."); return; }
      V.el("verbs-heading").textContent = c.title;
      V.el("verbs-sub").textContent = c.desc || "";
      const meaning = Object.fromEntries(idx.verbs.map((v) => [v.verb, v.meaning]));
      for (const v of c.verbs) item(`#/verbs/${encodeURIComponent(v)}`, v, meaning[v] || "");
    } catch { verbsError("Couldn't load the verbs. Are you online?"); }
    return;
  }
  V.el("verbs-back").href = V.lastList || "#/verbs";
  V.el("verbs-heading").textContent = verb;
  V.el("verbs-sub").textContent = "";
  if (V.kind !== "verb" || !V.data || V.data.verb !== verb) {
    resetVerbAudio();
    V.kind = "verb"; V.data = null;
    try { V.data = await (await fetch(`verbs/${encodeURIComponent(verb)}.json`, { cache: "no-cache" })).json(); }
    catch { if (token === V.token) verbsError("Couldn't load this verb. Are you online?"); return; }
    if (token !== V.token) return;
  }
  V.el("verbs-sub").textContent = V.data.meaning;
  renderVerbChoices();
}

// ---------- download all (verb data, pronouns and every clip) for offline use
// Kept in their own cache, which app updates never clear; sw.js serves clips from
// it first (they're named by a hash of their content, so they never change).
// Tapping again refetches the lists and only downloads clips not saved yet.

const VERBS_CACHE = "escucha-verbs";
let verbsDl = null;     // {done, total} while downloading
let verbsDlError = "";

function verbsSaved() {
  try { return JSON.parse(localStorage.getItem("verbsSaved")) || null; } catch { return null; }
}

function clipPaths(obj, out = new Set()) {
  if (typeof obj === "string") { if (/^clips\/[^/]+\.mp3$/.test(obj)) out.add(obj); }
  else if (obj && typeof obj === "object") for (const v of Object.values(obj)) clipPaths(v, out);
  return out;
}

async function downloadAllVerbs() {
  if (verbsDl || !("caches" in window)) return;
  verbsDl = { done: 0, total: 0 };
  verbsDlError = "";
  renderVerbsDl();
  try {
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
    const cache = await caches.open(VERBS_CACHE);
    const url = (path) => new URL(`verbs/${path}`, location.href).href;
    // ?dl= makes sw.js go straight to the network (no stale cached copy)
    const get = async (path) => {
      const res = await fetch(`${url(path)}?dl=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`${path}: ${res.status}`);
      return res;
    };
    const getJSON = async (path) => {
      const text = await (await get(path)).text();
      return { path, text, data: JSON.parse(text) };
    };
    const index = await getJSON("index.json");
    index.data = readIndex(index.data).verbs;  // just the verb list; the file itself is saved as is
    const docs = [index, await getJSON("pronouns.json")];
    for (const v of index.data) docs.push(await getJSON(`${encodeURIComponent(v.verb)}.json`));
    const clips = new Set();
    for (const d of docs) clipPaths(d.data, clips);
    verbsDl.total = clips.size;
    renderVerbsDl();
    const todo = [...clips];
    const worker = async () => {
      while (todo.length) {
        const path = todo.pop();
        if (!(await cache.match(url(path)))) {
          const blob = await (await get(path)).blob();
          await cache.put(url(path), new Response(blob, { headers: { "Content-Type": "audio/mpeg" } }));
        }
        verbsDl.done++;
        renderVerbsDl();
      }
    };
    await Promise.all(Array.from({ length: 6 }, worker));
    // the lists last, so they never point at clips that aren't saved
    for (const d of docs) await cache.put(url(d.path), new Response(d.text, { headers: { "Content-Type": "application/json" } }));
    try { localStorage.setItem("verbsSaved", JSON.stringify({ verbs: index.data.map((v) => v.verb), at: Date.now() })); } catch {}
  } catch (e) {
    verbsDlError = /quota/i.test(String(e)) ? "Not enough space on this device." : "Download stopped. Tap again when online to finish.";
  } finally {
    verbsDl = null;
    renderVerbsDl();
  }
}

let verbsListed = null;  // verbs shown in the list, to spot new ones
function renderVerbsDl(list) {
  if (list) verbsListed = list;
  const btn = V.el("verbs-dl-btn"), label = V.el("verbs-dl-label"), note = V.el("verbs-dl-note");
  const saved = verbsSaved();
  btn.disabled = !!verbsDl || !("caches" in window);
  btn.classList.toggle("done", !!saved && !verbsDl);
  label.textContent = verbsDl ? (verbsDl.total ? `Downloading… ${verbsDl.done} of ${verbsDl.total}` : "Downloading…")
    : saved ? "Downloaded · Check for new" : "Download all for offline";
  let text = verbsDlError;
  if (!text && saved && !verbsDl) {
    const fresh = (verbsListed || []).filter((v) => !saved.verbs.includes(v.verb)).length;
    text = fresh ? `${fresh} new verb${fresh > 1 ? "s" : ""} since your last download. Tap to get ${fresh > 1 ? "them" : "it"}.`
      : `${saved.verbs.length} verbs and the pronouns are on this device.`;
  }
  note.textContent = text;
  note.hidden = !text;
}

function verbsError(msg) {
  V.el("verbs-error").textContent = msg;
  V.el("verbs-error").hidden = false;
}

// groups: [{title, items: [{id, label}], picked: [ids], toggle(id, on)}]
function renderGroups(groups) {
  const box = V.el("drill-groups");
  box.innerHTML = "";
  for (const g of groups) {
    const sec = document.createElement("section");
    sec.innerHTML = `<h3 class="section-title"></h3><div class="chip-checks"></div>`;
    sec.querySelector("h3").textContent = g.title;
    const chips = sec.querySelector("div");
    for (const it of g.items) {
      const label = document.createElement("label");
      label.className = "chip-check";
      label.innerHTML = `<input type="checkbox"><span></span>`;
      label.querySelector("span").textContent = it.label;
      const input = label.querySelector("input");
      input.checked = g.picked.includes(it.id);
      input.onchange = () => { g.toggle(it.id, input.checked); selectionChanged(); };
      chips.append(label);
    }
    box.append(sec);
  }
  selectionChanged();
}

function renderVerbChoices() {
  const sel = verbSel();
  const group = (title, key, items, all) => ({
    title, items, picked: sel[key],
    toggle: (id, on) => {
      const s = verbSel();
      const picked = new Set(s[key]);
      on ? picked.add(id) : picked.delete(id);
      s[key] = all.filter((x) => picked.has(x));  // drill order
      saveVerbSel(s);
    },
  });
  renderGroups([
    // keep choices this verb doesn't show (haber has no preterite)
    group("Tenses", "tenses", V.data.tenses.map((t) => ({ id: t.id, label: t.label })),
          ["infinitive", "present", "preterite", "imperfect", "imperative"]),
    group("Pronouns", "pronouns", V.data.pronouns.map((p) => ({ id: p.id, label: p.es })),
          V.data.pronouns.map((p) => p.id)),
  ]);
}

function selectionKey() {
  const s = verbSel();
  if (V.kind === "pron") return `pron|${JSON.stringify(pronSel())}|${s.english}`;
  return `${V.data.verb}|${s.tenses}|${s.pronouns}|${s.english}`;
}

// The drill: [{clip, pause, es, en}] in play order. With English off only the
// Spanish is spoken; the English (intro / "She form.") still shows on screen.
function drillSteps() {
  if (V.kind === "pron") return pronSteps();
  const s = verbSel();
  const steps = [];
  for (const t of V.data.tenses.filter((t) => s.tenses.includes(t.id))) {
    if (steps.length) steps[steps.length - 1].pause += PAUSE_TENSE;
    if (s.english) steps.push({ clip: t.en_clip, pause: PAUSE_AFTER_CUE, en: t.en, es: "" });
    if (t.single) {  // infinitive: the verb itself, no pronouns
      steps.push({ clip: t.single.clip, pause: PAUSE_AFTER_ES, en: t.en, es: t.single.es });
      continue;
    }
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

// Pronouns: each picked item says its English sentence, then the Spanish.
function pronSteps() {
  const english = verbSel().english, sel = pronSel();
  const steps = [];
  for (const set of V.data.sets) {
    const items = set.items.filter((it) => sel[set.id].includes(it.id));
    if (items.length && steps.length) steps[steps.length - 1].pause += PAUSE_TENSE;
    for (const it of items) {
      if (english) steps.push({ clip: it.en_clip, pause: PAUSE_AFTER_CUE, en: it.en, es: "" });
      steps.push({ clip: it.clip, pause: PAUSE_AFTER_ES, en: it.en, es: it.es });
    }
  }
  return steps;
}

function selectionChanged() {
  if (!V.data) return;
  // Count what will actually play: haber has no preterite, commands only have
  // tú / usted / nosotros / nosotras / ustedes.
  const n = drillSteps().filter((x) => x.es).length;
  V.el("verb-play").disabled = !n;
  V.el("verb-hint").textContent = n ? `${n} sentence${n > 1 ? "s" : ""}`
    : V.kind === "pron" ? "Nothing to play. Pick at least one pronoun."
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
      navigator.mediaSession.metadata = V.kind === "pron"
        ? new MediaMetadata({ title: "Object pronouns", artist: "Pronouns" })
        : new MediaMetadata({ title: `${V.data.verb} · ${V.data.meaning}`, artist: "Verbs" });
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
  V.el("verbs-dl-btn").onclick = downloadAllVerbs;
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
