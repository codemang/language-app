// Verb drills and object-pronoun drills, on the same screen.
//
//   #/verbs                 the collections
//   #/verbs/c/<id>          one collection: Learn | Practice, All verbs / Choose my own
//   #/verbs/c/<id>/all      a session with every verb of the collection
//   #/verbs/c/<id>/pick     a session with the verbs picked in the popup
//   #/verbs/<verb>          a session with one verb
//   #/pronouns              the pronoun drill
//
// Learn plays the drill as audio; Practice (quiz) shows each English prompt and
// waits for you to say the Spanish before revealing it.
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
  data: null,        // {verbs: [verbs/<verb>.json...]} of the session, or verbs/pronouns.json
  title: "",         // session name for the lock screen
  cues: [],          // [{start, end, es, en}] of the part that's loaded
  builtFor: "",      // selection the current audio was built from
  url: null,
  seg: null,         // the drill in parts: {key, steps, parts: [[from, to]], i, offset, nextI, next}
  repeat: false,
  ctx: null,
  token: 0,
};
const PAUSE_AFTER_CUE = 0.6;  // English cue -> its Spanish (s)
const PAUSE_AFTER_ES = 1.8;   // after each Spanish sentence: time to repeat it (s)
const PAUSE_TENSE = 1.0;      // extra pause before the next tense (s)
const PART_STEPS = 30;        // clips per part: a long drill starts playing once its first part is ready
const BUNDLE_OVER = 6;        // sessions with more verbs than this load verbs/all.json instead

function verbSel() {
  let s = {};
  try { s = JSON.parse(localStorage.getItem("verbSel")) || {}; } catch {}
  return { tenses: s.tenses || ["present"], pronouns: s.pronouns || ["yo", "tu", "el", "ella", "nosotros", "ellos", "ellas"],
           english: s.english !== false, quiz: !!s.quiz,  // quiz: Practice mode
           read: !!s.read };  // Learn shows every sentence to read instead of the audio player
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
  showParts("drill");
  V.el("verbs-back").href = "#/";
  V.title = "Object pronouns";
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

// [verbs/<verb>.json] for these verbs. Many verbs: the one bundle (falls back
// to one file each if the bundle is missing or older than the index).
async function loadVerbData(verbs) {
  if (verbs.length > BUNDLE_OVER) {
    try {
      const res = await fetch("verbs/all.json", { cache: "no-cache" });
      if (res.ok) {
        const all = new Map((await res.json()).verbs.map((v) => [v.verb, v]));
        if (verbs.every((v) => all.has(v))) return verbs.map((v) => all.get(v));
      }
    } catch {}
  }
  return Promise.all(verbs.map(async (v) => {
    const res = await fetch(`verbs/${encodeURIComponent(v)}.json`, { cache: "no-cache" });
    if (!res.ok) throw new Error(v);
    return res.json();
  }));
}

// verbs/index.json: {verbs: [{verb, meaning}], collections: [{id, title, desc, verbs}]}
// (an older copy saved offline may still be the plain verb list)
function readIndex(idx) {
  return Array.isArray(idx) ? { verbs: idx, collections: [] } : idx;
}

// Which parts of the screen show: "top" (collections), "col" (one collection),
// "drill" (a session or the pronouns), "none" (still loading / error)
function showParts(part) {
  V.el("verbs-list").hidden = part !== "top";
  V.el("verbs-dl").hidden = part !== "col";
  V.el("col-page").hidden = part !== "col";
  V.el("verb-drill").hidden = part !== "drill";
  V.el("verb-mode").hidden = part !== "col" && part !== "drill";
  V.el("verbs-error").hidden = true;
  V.el("verbs-loading").hidden = true;
  renderVerbMode();
}

async function loadIndex() {
  V.index = readIndex(await (await fetch("verbs/index.json", { cache: "no-cache" })).json());
  return V.index;
}

function collection(idx, id) {
  if (id === "all") return { id, title: "All verbs", desc: "Every verb in the app", verbs: idx.verbs.map((v) => v.verb) };
  return idx.collections.find((x) => x.id === id);
}

const count = (n) => `${n} verb${n === 1 ? "" : "s"}`;

function pickedVerbs(c) {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(`verbPick:${c.id}`)); } catch {}
  const picked = Array.isArray(saved) ? c.verbs.filter((v) => saved.includes(v)) : [];
  return picked.length ? picked : c.verbs;
}

async function showVerbs(arg) {
  const token = ++V.token;
  // c/<id>/v/<verb>: one verb, opened from its collection's list
  const m = arg && arg.match(/^c\/([^/]+)(?:\/(all|pick|v\/[^/]+))?$/);
  const colId = m && m[1], session = m && m[2];
  const one = session && session.startsWith("v/") ? decodeURIComponent(session.slice(2)) : null;
  V.el("verbs-heading").textContent = "Verbs";
  V.el("verbs-sub").textContent = " ";
  if (!arg || (m && !session)) pauseVerbAudio();  // back from a player to a list: stop it
  if (!arg) {  // the collections
    showParts("top");
    V.el("verbs-back").href = "#/";
    V.el("verbs-sub").textContent = "Pick a collection";
  } else if (m && !session) {
    showParts("col");
    V.el("verbs-back").href = "#/verbs";
  } else {  // a session: say so while its verbs load
    showParts("none");
    V.el("verbs-loading").hidden = false;
  }
  let idx;
  try { idx = await loadIndex(); }
  catch { if (token === V.token) verbsError("Couldn't load the verbs. Are you online?"); return; }
  if (token !== V.token) return;

  if (!arg) {
    const ul = V.el("verbs-list");
    ul.innerHTML = "";
    listDl = [];
    const item = (c) => {
      const li = document.createElement("li");
      li.className = "col-row";
      li.innerHTML = `<a href="#/verbs/c/${c.id}"><span class="t"></span><span class="d"></span></a><button type="button" class="row-dl"></button>`;
      li.querySelector(".t").textContent = c.title;
      const btn = li.querySelector("button");
      btn.onclick = () => downloadCollection(c);
      listDl.push({ c, btn, detail: li.querySelector(".d") });
      ul.append(li);
    };
    for (const c of idx.collections.filter((c) => c.verbs.length)) item(c);
    item(collection(idx, "all"));
    renderVerbsDl();
    return;
  }

  let verbs, back;
  if (m) {
    const c = collection(idx, colId);
    if (!c) { verbsError("That collection doesn't exist."); return; }
    V.el("verbs-heading").textContent = c.title;
    if (!session) {  // the collection page
      V.el("verbs-sub").textContent = c.desc || "";
      V.el("col-all").href = `#/verbs/c/${c.id}/all`;
      V.el("col-all").textContent = `All ${count(c.verbs.length)}`;
      V.el("col-pick").onclick = () => openPicker(c, idx);
      V.el("verbs-dl-btn").onclick = () => downloadCollection(c);
      renderVerbsDl(c);
      const ul = V.el("col-verbs");
      ul.innerHTML = "";
      const meaning = new Map(idx.verbs.map((v) => [v.verb, v.meaning]));
      for (const v of c.verbs) {
        const li = document.createElement("li");
        li.innerHTML = `<a><span class="t"></span><span class="d"></span></a>`;
        li.querySelector("a").href = `#/verbs/c/${c.id}/v/${encodeURIComponent(v)}`;
        li.querySelector(".t").textContent = v;
        li.querySelector(".d").textContent = meaning.get(v) || "";
        ul.append(li);
      }
      return;
    }
    verbs = one ? [one] : session === "all" ? c.verbs : pickedVerbs(c);
    back = `#/verbs/c/${c.id}`;
    V.title = c.title;
    V.el("verbs-sub").textContent = verbs.length === c.verbs.length ? `All ${count(verbs.length)}` : verbs.join(", ");
    if (one) V.el("verbs-heading").textContent = one;
  } else {
    verbs = [arg];
    back = "#/verbs";
    V.el("verbs-heading").textContent = arg;
  }
  V.el("verbs-back").href = back;
  if (V.kind !== "verb" || !V.data || V.data.verbs.map((v) => v.verb).join() !== verbs.join()) {
    resetVerbAudio();
    V.kind = "verb"; V.data = null;
    V.el("verb-mode").hidden = false;  // the header and the switch show while it loads
    V.el("verbs-loading").textContent = verbs.length > 1 ? `Loading ${count(verbs.length)}…` : "Loading…";
    try {
      V.data = { verbs: await loadVerbData(verbs) };
    } catch { if (token === V.token) verbsError("Couldn't load the verbs. Are you online?"); return; }
    if (token !== V.token) return;
  }
  if (!m || one) {
    V.title = `${verbs[0]} · ${V.data.verbs[0].meaning}`;
    V.el("verbs-sub").textContent = V.data.verbs[0].meaning;
  }
  showParts("drill");
  renderVerbChoices();
}

// The "Choose my own verbs" popup: every verb of the collection, ticked as last time.
function openPicker(c, idx) {
  const dlg = V.el("pick-dialog"), ul = V.el("pick-list"), start = V.el("pick-start");
  const meaning = Object.fromEntries(idx.verbs.map((v) => [v.verb, v.meaning]));
  const picked = new Set(pickedVerbs(c));
  ul.innerHTML = "";
  for (const v of c.verbs) {
    const li = document.createElement("li");
    li.innerHTML = `<label><input type="checkbox"><span class="t"></span><span class="d"></span></label>`;
    li.querySelector("input").checked = picked.has(v);
    li.querySelector("input").value = v;
    li.querySelector(".t").textContent = v;
    li.querySelector(".d").textContent = meaning[v] || "";
    ul.append(li);
  }
  const boxes = () => [...ul.querySelectorAll("input")];
  const update = () => { start.disabled = !boxes().some((b) => b.checked); };
  ul.onchange = update;
  V.el("pick-all").onclick = () => { boxes().forEach((b) => (b.checked = true)); update(); };
  V.el("pick-none").onclick = () => { boxes().forEach((b) => (b.checked = false)); update(); };
  update();
  dlg.onclose = () => {
    if (dlg.returnValue !== "start") return;
    const chosen = boxes().filter((b) => b.checked).map((b) => b.value);
    try { localStorage.setItem(`verbPick:${c.id}`, JSON.stringify(chosen)); } catch {}
    location.hash = `#/verbs/c/${c.id}/pick`;
  };
  dlg.returnValue = "";
  dlg.showModal();
}

// Learn | Practice
function renderVerbMode() {
  const quiz = verbSel().quiz;
  V.el("mode-learn").setAttribute("aria-checked", !quiz);
  V.el("mode-practice").setAttribute("aria-checked", quiz);
  const help = V.el("verb-mode-help");
  help.hidden = V.el("col-page").hidden;  // explain on the collection page only
  help.textContent = quiz ? "Hear the English, say the Spanish, then tap → to check."
    : "Listen to every sentence in English and Spanish.";
}

function setVerbMode(quiz) {
  const s = verbSel(); s.quiz = quiz; saveVerbSel(s);
  renderVerbMode();
  setQuiz(quiz);
  if (V.data) renderOptsSummary();
}

// ---------- download a collection (its verbs, the pronouns, and their clips) for offline use
// Kept in their own cache, which app updates never clear; sw.js serves clips from
// it first (they're named by a hash of their content, so they never change).
// Tapping again refetches the lists and only downloads clips not saved yet.
// localStorage "verbsSaved" {verbs, at}: every verb downloaded so far, from any
// collection, so a collection counts as downloaded once all its verbs are.

const VERBS_CACHE = "escucha-verbs";
let verbsDl = null;     // {id, done, total} while downloading
let verbsDlError = "";  // {id, msg} of the last failed download
let dlCol = null;       // the collection whose page is showing
let listDl = [];        // [{c, btn, detail}]: the download buttons on the collections list

function savedVerbs() {
  try { return new Set((JSON.parse(localStorage.getItem("verbsSaved")) || {}).verbs || []); } catch { return new Set(); }
}
const colSaved = (c) => { const saved = savedVerbs(); return c.verbs.every((v) => saved.has(v)); };

function clipPaths(obj, out = new Set()) {
  if (typeof obj === "string") { if (/^clips\/[^/]+\.mp3$/.test(obj)) out.add(obj); }
  else if (obj && typeof obj === "object") for (const v of Object.values(obj)) clipPaths(v, out);
  return out;
}

async function downloadCollection(c) {
  if (verbsDl || !("caches" in window)) return;
  verbsDl = { id: c.id, done: 0, total: 0 };
  verbsDlError = null;
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
    // The lists (index and bundle) are saved too, so pages open offline; only
    // this collection's verbs and the pronouns bring their clips.
    const lists = [await getJSON("index.json")];
    let bundle = null;
    try { bundle = await getJSON("all.json"); lists.push(bundle); } catch {}
    const inBundle = new Map(bundle ? bundle.data.verbs.map((v) => [v.verb, v]) : []);
    const docs = [await getJSON("pronouns.json")];
    for (const v of c.verbs) {
      const path = `${encodeURIComponent(v)}.json`, data = inBundle.get(v);
      docs.push(data ? { path, text: JSON.stringify(data), data } : await getJSON(path));
    }
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
    for (const d of [...docs, ...lists]) await cache.put(url(d.path), new Response(d.text, { headers: { "Content-Type": "application/json" } }));
    const saved = savedVerbs();
    c.verbs.forEach((v) => saved.add(v));
    try { localStorage.setItem("verbsSaved", JSON.stringify({ verbs: [...saved], at: Date.now() })); } catch {}
  } catch (e) {
    verbsDlError = { id: c.id, msg: /quota/i.test(String(e)) ? "Not enough space on this device." : "Download stopped. Tap again when online to finish." };
  } finally {
    verbsDl = null;
    renderVerbsDl();
  }
}

const DL_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 4h2v9.2l3.6-3.6L18 11l-6 6-6-6 1.4-1.4 3.6 3.6zM5 18h14v2H5z"/></svg>`;
const DONE_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"/></svg>`;

// The download buttons: one per row on the collections list, and the one on a collection's page
function renderVerbsDl(c) {
  const saved = savedVerbs();
  for (const row of listDl) {
    const missing = row.c.verbs.filter((v) => !saved.has(v)).length;
    const mine = verbsDl && verbsDl.id === row.c.id;
    const failed = verbsDlError && verbsDlError.id === row.c.id;
    row.detail.textContent = mine ? (verbsDl.total ? `Downloading… ${Math.floor((100 * verbsDl.done) / verbsDl.total)}%` : "Downloading…")
      : failed ? "Download stopped" : count(row.c.verbs.length) + (missing ? "" : " · offline");
    row.btn.disabled = !!verbsDl || !("caches" in window);
    row.btn.classList.toggle("done", !missing && !mine);
    row.btn.classList.toggle("busy", !!mine);
    row.btn.innerHTML = !missing && !mine ? DONE_ICON : DL_ICON;
    row.btn.setAttribute("aria-label", !missing ? `${row.c.title}: downloaded, tap to check for updates` : `Download ${row.c.title} for offline`);
  }
  if (c) dlCol = c;
  c = dlCol;
  if (!c) return;
  const btn = V.el("verbs-dl-btn"), label = V.el("verbs-dl-label"), note = V.el("verbs-dl-note");
  const missing = c.verbs.filter((v) => !saved.has(v)).length;
  const mine = verbsDl && verbsDl.id === c.id;
  btn.disabled = !!verbsDl || !("caches" in window);
  btn.classList.toggle("done", !missing && !verbsDl);
  label.textContent = mine ? (verbsDl.total ? `Downloading… ${verbsDl.done} of ${verbsDl.total}` : "Downloading…")
    : verbsDl ? "Another download is running…"
    : !missing ? "Downloaded for offline"
    : missing < c.verbs.length ? `Download ${missing} more verb${missing > 1 ? "s" : ""} for offline`
    : "Download for offline";
  const text = verbsDlError && verbsDlError.id === c.id ? verbsDlError.msg : "";
  note.textContent = text;
  note.hidden = !text;
}

function verbsError(msg) {
  V.el("verbs-loading").hidden = true;
  V.el("verbs-error").textContent = msg;
  V.el("verbs-error").hidden = false;
}

// groups: [{title, items: [{id, label}], picked: [ids], toggle(id, on)}]
function renderGroups(groups) {
  V.groups = groups;
  const box = V.el("drill-groups");
  box.innerHTML = "";
  for (const g of groups) {
    const sec = document.createElement("section");
    sec.innerHTML = `<div class="group-head"><h3 class="section-title"></h3>
      <button type="button" class="group-bulk">Select all</button><button type="button" class="group-bulk">Deselect all</button></div>
      <div class="chip-checks"></div>`;
    sec.querySelector("h3").textContent = g.title;
    const chips = sec.querySelector(".chip-checks");
    const [all, none] = sec.querySelectorAll(".group-bulk");
    const setAll = (on) => {
      for (const [i, input] of [...chips.querySelectorAll("input")].entries()) {
        if (input.checked === on) continue;
        input.checked = on;
        g.toggle(g.items[i].id, on);
      }
      g.picked = on ? g.items.map((it) => it.id) : [];
      selectionChanged();
    };
    all.onclick = () => setAll(true);
    none.onclick = () => setAll(false);
    for (const it of g.items) {
      const label = document.createElement("label");
      label.className = "chip-check";
      label.innerHTML = `<input type="checkbox"><span></span>`;
      label.querySelector("span").textContent = it.label;
      const input = label.querySelector("input");
      input.checked = g.picked.includes(it.id);
      input.onchange = () => {
        g.toggle(it.id, input.checked);
        g.picked = input.checked ? [...g.picked, it.id] : g.picked.filter((x) => x !== it.id);
        selectionChanged();
      };
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
  const ALL_TENSES = ["infinitive", "present", "preterite", "imperfect", "imperative"];
  const tenses = new Map();  // every tense any of the verbs has, in drill order
  for (const v of V.data.verbs) for (const t of v.tenses) tenses.set(t.id, t.label);
  const pronouns = V.data.verbs[0].pronouns;
  renderGroups([
    // keep choices these verbs don't show (haber has no preterite)
    group("Tenses", "tenses", ALL_TENSES.filter((id) => tenses.has(id)).map((id) => ({ id, label: tenses.get(id) })), ALL_TENSES),
    group("Pronouns", "pronouns", pronouns.map((p) => ({ id: p.id, label: p.es })), pronouns.map((p) => p.id)),
  ]);
}

function selectionKey() {
  const s = verbSel();
  if (V.kind === "pron") return `pron|${JSON.stringify(pronSel())}|${s.english}`;
  return `${V.data.verbs.map((v) => v.verb)}|${s.tenses}|${s.pronouns}|${s.english}`;
}

// The drill: [{clip, pause, es, en}] in play order. With English off only the
// Spanish is spoken; the English (intro / "She form.") still shows on screen.
function drillSteps() {
  if (V.kind === "pron") return pronSteps();
  const s = verbSel();
  const steps = [];
  for (const v of V.data.verbs) for (const t of v.tenses.filter((t) => s.tenses.includes(t.id))) {
    if (steps.length) steps[steps.length - 1].pause += PAUSE_TENSE;
    if (s.english) steps.push({ clip: t.en_clip, pause: PAUSE_AFTER_CUE, en: t.en, es: "" });
    if (t.single) {  // infinitive: the verb itself, no pronouns
      steps.push({ clip: t.single.clip, pause: PAUSE_AFTER_ES, en: t.en, es: t.single.es });
      continue;
    }
    // Commands only have tú / usted / nosotros / nosotras / ustedes forms.
    v.pronouns.filter((p) => s.pronouns.includes(p.id) && t.forms[p.id]).forEach((p, i) => {
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

// The one-line-per-group summary shown on the drill page; tap it to edit.
function renderOptsSummary() {
  const box = V.el("opts-lines");
  box.innerHTML = "";
  const line = (title, text) => {
    const p = document.createElement("span");
    p.innerHTML = "<b></b> <span></span>";
    p.querySelector("b").textContent = title;
    p.lastChild.textContent = text;
    box.append(p);
  };
  for (const g of V.groups || []) {
    const picked = g.items.filter((it) => g.picked.includes(it.id)).map((it) => it.label.split(" · ")[0]);
    line(g.title, !picked.length ? "None"
      : picked.length === g.items.length && g.items.length > 2 ? "All"
      : picked.length > 4 ? `${picked.slice(0, 3).join(", ")} +${picked.length - 3}`
      : picked.join(", "));
  }
  line("English", verbSel().english ? "Spoken" : "Not spoken");
}

function selectionChanged() {
  if (!V.data) return;
  renderOptsSummary();
  // Count what will actually play: haber has no preterite, commands only have
  // tú / usted / nosotros / nosotras / ustedes.
  const n = drillSteps().filter((x) => x.es).length;
  V.el("verb-play").disabled = !n;
  V.el("verb-hint").textContent = n ? `${n} sentence${n > 1 ? "s" : ""}`
    : V.kind === "pron" ? "Nothing to play. Pick at least one pronoun."
    : "Nothing to play. Pick a tense, and a pronoun it has (commands: Tú, Usted, Nosotros, Nosotras, Ustedes).";
  if (V.builtFor && V.builtFor !== selectionKey()) resetVerbAudio();
  // Quiz restarts when the picks change (not when "Say the English" does)
  if (verbSel().quiz) V.quiz.key !== quizKey() ? quizStart() : quizRender();
  else if (verbSel().read) renderRead();
}

// ---------- Quiz mode
// One sentence at a time: the English prompt shows (and is spoken, with "Say the
// English" on), you say the Spanish out loud, -> reveals and plays the Spanish,
// -> again moves to the next prompt. Same tenses / pronouns as the drill.

V.quiz = { cards: [], i: 0, answer: false, key: "" };
const quizKey = () => selectionKey().replace(/\|(true|false)$/, "");

// [{ctx, en, en_clip, es, clip}]
function quizCards() {
  const cards = [];
  if (V.kind === "pron") {
    const sel = pronSel();
    for (const set of V.data.sets) for (const it of set.items.filter((it) => sel[set.id].includes(it.id)))
      cards.push({ ctx: set.title, en: it.en, en_clip: it.en_clip, es: it.es, clip: it.clip, group: set.title, read_en: it.en, parts: it.parts });
    return cards;
  }
  const s = verbSel(), several = V.data.verbs.length > 1;
  for (const v of V.data.verbs) for (const t of v.tenses.filter((t) => s.tenses.includes(t.id))) {
    const tense = several ? `${v.verb} · ${t.label}` : t.label;  // which verb, in a mixed session
    if (t.single) {
      const en = t.en.replace(/^Infinitive: /, "");
      cards.push({ ctx: tense, en, en_clip: t.en_clip, es: t.single.es, clip: t.single.clip, group: tense, read_en: en, parts: t.single.parts });
      continue;
    }
    // The tense intro is the yo sentence; other pronouns get their cue ("She form."),
    // with the intro shown above as the model sentence.
    for (const p of v.pronouns.filter((p) => s.pronouns.includes(p.id) && t.forms[p.id])) {
      const f = t.forms[p.id], model = t.en.replace(/^[^:]*: /, "");
      const read = { group: tense, read_en: f.en || (p.id === "yo" ? model : p.cue), parts: f.parts };
      cards.push(p.id === "yo" ? { ctx: tense, en: model, en_clip: t.en_clip, es: f.es, clip: f.clip, ...read }
        : { ctx: `${tense} · ${model}`, en: p.cue, en_clip: p.cue_clip, es: f.es, clip: f.clip, ...read });
    }
  }
  return cards;
}

function quizStart() {
  V.quiz = { cards: quizCards(), i: 0, answer: false, key: quizKey() };
  quizRender();
}

// ---------- Learn, Read all: every sentence at once, Spanish over English,
// the pronoun and the verb in color (from each sentence's "parts"). Tap one to hear it.
// Uses the same sentences as Practice (quizCards), but lives under Learn.

function setRead(on) {
  const s = verbSel(); s.read = on; saveVerbSel(s);
  if (V.audio) V.audio.pause();
  if (V.clipAudio) V.clipAudio.pause();
  renderReadView();
  if (on) renderRead(); else if (V.data) selectionChanged();
}

function renderReadView() {
  const learn = !verbSel().quiz, read = learn && verbSel().read;
  V.el("learn-view").hidden = !learn;
  V.el("view-listen").setAttribute("aria-checked", !read);
  V.el("view-read").setAttribute("aria-checked", read);
  V.el("learn-read").hidden = !read;
  if (learn) V.el("verb-sentence").hidden = V.el("verb-controls").hidden = V.el("verb-time").hidden = read;
}

function spanishParts(el, c) {
  if (!c.parts) { el.textContent = c.es; return; }
  c.parts.forEach(([text, kind], i) => {
    if (i) el.append(" ");
    if (!kind) { el.append(text); return; }
    const [, word, punct] = text.match(/^(.*?)([.,;:!?]*)$/);  // the full stop stays uncolored
    const span = document.createElement("span");
    span.className = kind === "p" ? "hl-p" : "hl-v";
    span.textContent = word;
    el.append(span, punct);
  });
}

function renderRead() {
  if (!V.data) return;
  const cards = quizCards(), list = V.el("learn-read-list");
  list.innerHTML = "";
  let group = null;
  for (const c of cards) {
    if (c.group !== group) {
      group = c.group;
      const h = document.createElement("h3");
      h.className = "read-group";
      h.textContent = group;
      list.append(h);
    }
    const row = document.createElement("button");
    row.type = "button";
    row.className = "read-row";
    row.innerHTML = '<span class="read-es"></span><span class="read-en"></span>';
    spanishParts(row.firstChild, c);
    row.lastChild.textContent = c.read_en || c.en;
    row.onclick = () => {
      list.querySelectorAll(".playing").forEach((r) => r.classList.remove("playing"));
      row.classList.add("playing");
      quizSay(c.clip);
    };
    list.append(row);
  }
  if (!cards.length) list.innerHTML = '<p class="sub">Nothing picked. Tap the options above to choose tenses and pronouns.</p>';
  V.el("verb-hint").textContent = `${cards.length} sentence${cards.length === 1 ? "" : "s"}`;
  renderReadView();
}

function quizRender() {
  const P = V.quiz, c = P.cards[P.i];
  const es = V.el("quiz-es");
  V.el("quiz-prev").disabled = P.i === 0;
  V.el("quiz-next").disabled = !P.cards.length;
  V.el("quiz-replay").disabled = !c;
  if (!c) {  // past the last card (or nothing picked)
    V.el("quiz-ctx").textContent = P.cards.length ? "Done" : " ";
    V.el("quiz-en").textContent = P.cards.length ? "All done!" : " ";
    es.textContent = P.cards.length ? "Tap → to start again" : " ";
    es.classList.add("waiting");
    V.el("verb-hint").textContent = P.cards.length ? `${P.cards.length} of ${P.cards.length}` : V.el("verb-hint").textContent;
    return;
  }
  V.el("quiz-ctx").textContent = c.ctx;
  V.el("quiz-en").textContent = c.en;
  es.textContent = P.answer ? c.es : "Say it in Spanish, then tap →";
  es.classList.toggle("waiting", !P.answer);
  V.el("verb-hint").textContent = `${P.i + 1} of ${P.cards.length}`;
}

function quizSay(clip) {
  const a = V.clipAudio || (V.clipAudio = new Audio());
  a.src = `verbs/${clip}`;
  a.playbackRate = Number(localStorage.getItem("speed")) || 1;
  a.play().catch(() => {});
}

function quizPrompt() {  // show the current prompt, speaking it if "Say the English" is on
  quizRender();
  const c = V.quiz.cards[V.quiz.i];
  if (c && verbSel().english) quizSay(c.en_clip);
}

function quizNext() {
  const P = V.quiz;
  if (!P.cards.length) return;
  if (P.i >= P.cards.length) { P.i = 0; P.answer = false; quizPrompt(); return; }
  if (!P.answer) {  // reveal
    P.answer = true;
    quizRender();
    quizSay(P.cards[P.i].clip);
    return;
  }
  P.i++; P.answer = false;
  if (P.i >= P.cards.length) { if (V.clipAudio) V.clipAudio.pause(); quizRender(); return; }
  quizPrompt();
}

function quizPrev() {
  const P = V.quiz;
  if (P.i === 0) return;
  P.i--; P.answer = false;
  quizPrompt();
}

function quizReplay() {
  const c = V.quiz.cards[V.quiz.i];
  if (c) quizSay(V.quiz.answer ? c.clip : c.en_clip);
}

function setQuiz(on) {
  V.el("quiz").hidden = !on;
  V.el("verb-sentence").hidden = on;
  V.el("verb-controls").hidden = on;
  V.el("verb-time").hidden = on;
  if (on) { if (V.audio) V.audio.pause(); if (V.data) quizStart(); }
  else { if (V.clipAudio) V.clipAudio.pause(); V.quiz.key = ""; if (V.data) selectionChanged(); }
  renderReadView();
}

function resetVerbAudio() {
  const a = V.audio;
  if (a) { a.pause(); a.removeAttribute("src"); a.load(); }
  if (V.url) URL.revokeObjectURL(V.url);
  V.url = null; V.builtFor = ""; V.cues = []; V.seg = null;
  V.el("verb-play").classList.remove("playing");
  V.el("verb-es").textContent = " ";
  V.el("verb-en").textContent = " ";
  V.el("verb-time").textContent = "";
}

async function decodeClip(path, memo) {
  if (!memo.has(path)) memo.set(path, fetch(`verbs/${path}`).then((r) => r.arrayBuffer()).then((b) => V.ctx.decodeAudioData(b)));
  return memo.get(path);
}

// Join these steps' clips and pauses into one mono 16-bit WAV.
async function buildDrill(steps) {
  V.ctx = V.ctx || new (window.AudioContext || window.webkitAudioContext)();
  const memo = new Map();  // per part: a whole long drill decoded at once would be hundreds of MB
  const bufs = await Promise.all(steps.map((s) => decodeClip(s.clip, memo)));
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

// Long drills play in parts of about PART_STEPS clips, each cut after a Spanish
// sentence (in its pause), so playback starts as soon as the first part is
// built and the next one builds while this one plays.
function splitParts(steps) {
  const parts = [];
  let from = 0;
  steps.forEach((s, i) => { if (i + 1 - from >= PART_STEPS && s.es) { parts.push([from, i + 1]); from = i + 1; } });
  if (from < steps.length) parts.push([from, steps.length]);
  return parts;
}

const buildPart = (seg, i) => buildDrill(seg.steps.slice(...seg.parts[i]));

async function playPart(i, autoplay = true) {
  const seg = V.seg, a = V.audio;
  const built = (seg.nextI === i && (await seg.next)) || (await buildPart(seg, i));
  if (V.seg !== seg || selectionKey() !== seg.key) return;  // changed while it built
  if (V.url) URL.revokeObjectURL(V.url);
  V.url = URL.createObjectURL(built.blob);
  V.cues = built.cues;
  V.builtFor = seg.key;
  seg.i = i;
  if (i === 0) seg.offset = 0;
  a.src = V.url;
  a.loop = V.repeat && seg.parts.length === 1;  // more parts: "ended" moves on, and wraps when repeating
  a.playbackRate = Number(localStorage.getItem("speed")) || 1;
  seg.nextI = i + 1;
  seg.next = i + 1 < seg.parts.length ? buildPart(seg, i + 1).catch(() => null) : null;
  if (autoplay) await a.play();
}

async function verbPartEnded() {
  const seg = V.seg;
  if (!seg || seg.parts.length < 2) return;
  const last = seg.i === seg.parts.length - 1;
  if (!last) seg.offset += V.audio.duration;
  try { await playPart(last ? 0 : seg.i + 1, !last || V.repeat); }  // at the end: back to the start, paused
  catch { verbsError("Couldn't prepare the audio. Are you online?"); }
}

async function verbPlay() {
  const a = V.audio;
  const key = selectionKey();
  if (V.builtFor === key && a.src) { a.paused ? a.play() : a.pause(); return; }
  const btn = V.el("verb-play");
  btn.disabled = true;
  V.el("verb-hint").textContent = "Preparing…";
  try {
    resetVerbAudio();
    const steps = drillSteps();
    V.seg = { key, steps, parts: splitParts(steps), i: 0, offset: 0, nextI: -1, next: null };
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({ title: V.title, artist: V.kind === "pron" ? "Pronouns" : "Verbs" });
    }
    await playPart(0);
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
  V.el("verb-es").textContent = c.es || " ";
  V.el("verb-en").textContent = c.en || " ";
  const fmtT = (x) => `${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, "0")}`;
  const seg = V.seg;
  V.el("verb-time").textContent = seg && seg.parts.length > 1
    ? `${fmtT(seg.offset + t)} · part ${seg.i + 1} of ${seg.parts.length}`
    : `${fmtT(t)} / ${fmtT(V.audio.duration || 0)}`;
}

function initVerbs() {
  V.audio = V.el("verb-audio");
  V.el("verb-play").onclick = verbPlay;
  V.audio.addEventListener("timeupdate", verbTick);
  V.audio.addEventListener("ended", verbPartEnded);
  V.audio.addEventListener("play", () => V.el("verb-play").classList.add("playing"));
  V.audio.addEventListener("pause", () => V.el("verb-play").classList.remove("playing"));
  // Say English: off = Spanish sentences only
  const eng = V.el("verb-english");
  eng.checked = verbSel().english;
  eng.onchange = () => { const s = verbSel(); s.english = eng.checked; saveVerbSel(s); if (V.data) selectionChanged(); };
  // Repeat: loop the drill until it's turned off. Remembered between visits.
  const rep = V.el("verb-repeat");
  const setRepeat = (on) => {
    V.repeat = on;
    V.audio.loop = on && (!V.seg || V.seg.parts.length === 1);
    rep.setAttribute("aria-pressed", on);
    try { localStorage.setItem("verbRepeat", on ? "1" : ""); } catch {}
  };
  let saved = false;
  try { saved = localStorage.getItem("verbRepeat") === "1"; } catch {}
  setRepeat(saved);
  rep.onclick = () => setRepeat(!V.repeat);
  V.el("drill-opts").onclick = () => V.el("opts-dialog").showModal();
  V.el("opts-dialog").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });  // tap outside
  // Learn | Practice
  setQuiz(verbSel().quiz);
  V.el("mode-learn").onclick = () => setVerbMode(false);
  V.el("mode-practice").onclick = () => setVerbMode(true);
  V.el("quiz-next").onclick = quizNext;
  V.el("quiz-prev").onclick = quizPrev;
  V.el("quiz-replay").onclick = quizReplay;
  V.el("view-listen").onclick = () => setRead(false);
  V.el("view-read").onclick = () => setRead(true);
  document.addEventListener("keydown", (e) => {
    if (V.el("verb-drill").hidden || V.el("quiz").hidden || (e.target.closest && e.target.closest("input, textarea"))) return;
    if (e.key === "ArrowRight") { e.preventDefault(); quizNext(); }
    if (e.key === "ArrowLeft") { e.preventDefault(); quizPrev(); }
  });
  V.el("verb-restart").onclick = () => {
    if (!V.audio.src) return;
    if (V.seg && V.seg.i > 0) { V.audio.pause(); playPart(0).catch(() => {}); return; }
    V.audio.currentTime = 0; V.audio.play();
  };
}

function pauseVerbAudio() {
  if (V.audio) V.audio.pause();
  if (V.clipAudio) V.clipAudio.pause();
}

function leaveVerbs() {
  pauseVerbAudio();
  V.token++;
}
