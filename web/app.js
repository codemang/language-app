// Escucha — browse Dreaming Spanish videos, play them as Spanish-only or
// Spanish/English interleaved audio.
//
// Routes:  #/            video list (data/videos.json)
//          #/play/<id>   player
//
// Each video is converted once on the server into lessons/<video id>/: es.mp3,
// mix.mp3 and lesson.json with per-sentence timings in both: orig = [start, end] in es.mp3, mix_es / mix_en =
// where the Spanish clip and its English translation sit in mix.mp3. Switching
// modes maps the position through those timings so you stay on the same sentence.

const $ = (id) => document.getElementById(id);
const audio = $("audio");
const SKIP = 5;
const SPEEDS = [0.75, 0.9, 1, 1.25, 1.5];
const WATCHED_AT = 0.9;  // a lesson counts as watched once you're 90% through it

let videos = [];     // data/videos.json
let video = null;    // video currently open in the player
let lesson = null;   // lesson.json of the audio being played
let mode = "es";     // "es" | "mix"
let revealEn = false;

// ---------- helpers

function fmt(t) {
  t = Math.max(0, Math.floor(t || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

// ---------- video list

// "· 0.4 of 3 GB used" next to the list heading. Oldest-played lessons are deleted past the cap.
async function showStorage() {
  try {
    const { used, cap } = await getJSON("api/storage");
    const gb = (b) => (b / 1024 ** 3).toFixed(b < 1024 ** 3 ? 2 : 1);
    $("storage").textContent = ` · ${gb(used)} of ${gb(cap)} GB used`;
    $("storage").title = "When full, the lessons you played longest ago are deleted to make room.";
  } catch { /* offline: leave it out */ }
}

// ---------- offline downloads (Cache Storage; sw.js serves them, with Range support)

const OFFLINE = "escucha-offline";
const LESSON_FILES = ["lesson.json", "es.mp3", "mix.mp3"];
const downloaded = new Set();  // video ids saved on this device
let downloading = null;         // { id, pct } while a download runs

const lessonUrl = (id, f) => new URL(`lessons/${encodeURIComponent(id)}/${f}`, location.href).href;

async function loadDownloaded() {
  if (!("caches" in window)) return;
  try {
    const keys = await (await caches.open(OFFLINE)).keys();
    for (const r of keys) {
      const m = new URL(r.url).pathname.match(/\/lessons\/([^/]+)\/lesson\.json$/);
      if (m) downloaded.add(decodeURIComponent(m[1]));
    }
  } catch { /* storage blocked: downloads just aren't available */ }
}

async function downloadLesson(v) {
  if (downloading || !("caches" in window)) return;
  downloading = { id: v.id, pct: 0 };
  renderDownload();
  try {
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
    // Fetch everything first, so a failed download leaves nothing half-saved.
    const sizes = LESSON_FILES.map(() => 0), loaded = LESSON_FILES.map(() => 0);
    const blobs = await Promise.all(LESSON_FILES.map(async (f, i) => {
      const res = await fetch(lessonUrl(v.id, f) + `?dl=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`${f}: ${res.status}`);
      sizes[i] = +res.headers.get("Content-Length") || 0;
      const reader = res.body.getReader(), chunks = [];
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        loaded[i] += value.length;
        const total = sizes.reduce((a, b) => a + b, 0);
        if (total && downloading) {
          downloading.pct = Math.min(99, Math.round((100 * loaded.reduce((a, b) => a + b, 0)) / total));
          renderDownload();
        }
      }
      return new Blob(chunks, { type: res.headers.get("Content-Type") || "" });
    }));
    const cache = await caches.open(OFFLINE);
    await Promise.all(LESSON_FILES.map((f, i) => cache.put(lessonUrl(v.id, f), new Response(blobs[i], {
      headers: { "Content-Type": blobs[i].type, "Content-Length": String(blobs[i].size) },
    }))));
    try {  // thumbnail, so the list looks right offline
      const thumb = await fetch(v.thumbnail);
      if (thumb.ok) await cache.put(new URL(v.thumbnail, location.href).href, thumb);
    } catch {}
    downloaded.add(v.id);
    downloadNote("");
  } catch (e) {
    downloadNote(/quota/i.test(String(e)) ? "Not enough space on this device." : "Download failed. Try again when online.");
  } finally {
    downloading = null;
    renderDownload();
  }
}

async function removeDownload(v) {
  if (!confirm("Remove this lesson from this device? You can download it again later.")) return;
  try {
    const cache = await caches.open(OFFLINE);
    await Promise.all(LESSON_FILES.map((f) => cache.delete(lessonUrl(v.id, f))));
  } catch {}
  downloaded.delete(v.id);
  downloadNote("");
  renderDownload();
}

function downloadNote(text) {
  $("download-note").textContent = text;
  $("download-note").hidden = !text;
}

function renderDownload() {
  if (!video) return;
  const btn = $("download"), label = $("download-label");
  const mine = downloading && downloading.id === video.id;
  btn.disabled = !!downloading || !("caches" in window);
  btn.classList.toggle("done", downloaded.has(video.id));
  label.textContent = mine ? `Downloading… ${downloading.pct}%`
    : downloaded.has(video.id) ? "On this device · Remove"
    : "Download for offline";
}

$("download").onclick = () => {
  if (!video || !lesson) return;
  downloaded.has(video.id) ? removeDownload(video) : downloadLesson(video);
};

// Lower case, accents stripped: "Mañana" matches "manana".
const fold = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function renderList() {
  const ul = $("videos");
  ul.innerHTML = "";
  $("list-empty").hidden = videos.length > 0;
  const words = fold($("search").value).split(/\s+/).filter(Boolean);
  const hideWatched = $("hide-watched").checked;
  // Offline, only downloaded videos can play, so nothing else is listed.
  const offline = !navigator.onLine;
  const onlyDownloaded = offline || $("only-downloaded").checked;
  $("offline-note").hidden = !offline;
  $("only-downloaded").closest("label").hidden = offline;  // the note says it all
  const shown = videos.filter((v) => {
    if (hideWatched && loadState(v.id).watched) return false;
    if (onlyDownloaded && !downloaded.has(v.id)) return false;
    const title = fold(v.title);
    return words.every((w) => title.includes(w));
  });
  $("list-nomatch").hidden = !videos.length || shown.length > 0;
  $("list-nomatch").textContent = offline && !downloaded.size
    ? "Nothing is downloaded on this device yet. When you're online, open a lesson and tap “Download for offline”."
    : onlyDownloaded && !downloaded.size
    ? "Nothing downloaded yet. Open a lesson and tap “Download for offline”."
    : offline ? "No downloaded videos match." : "No videos match.";
  for (const v of shown) {
    const st = loadState(v.id);
    const li = document.createElement("li");
    li.innerHTML = `
      <a class="card" href="#/play/${encodeURIComponent(v.id)}">
        <div class="thumb">
          <img loading="lazy" alt="">
          ${st.watched ? '<span class="badge watched">✓ Watched</span>' : ""}
          ${downloaded.has(v.id) ? '<span class="badge saved" title="Downloaded for offline">Offline</span>' : ""}
          <span class="badge">${fmt(v.duration)}</span>
          ${!st.watched && st.frac > 0.02 ? `<div class="thumb-progress" style="width:${Math.round(st.frac * 100)}%"></div>` : ""}
        </div>
        <div class="card-body">
          <h3></h3>
          <p class="sub"><span class="diff" title="Difficulty (0–100)">${v.difficulty}</span> ${v.level}</p>
        </div>
      </a>`;
    li.querySelector("img").src = v.thumbnail;
    li.querySelector("h3").textContent = v.title;
    ul.append(li);
  }
}

// ---------- position mapping

// Last sentence whose `key` range starts at or before t.
function sentenceAt(t, key) {
  const s = lesson.sentences;
  let lo = 0, hi = s.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (s[mid][key][0] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

function esToMix(t) {
  const i = sentenceAt(t, "orig");
  if (i < 0) return 0;  // before the first sentence (intro): start of mix
  const s = lesson.sentences[i];
  const off = t - s.orig[0];
  // Inside the Spanish clip: same spot in the mix. Past it (a pause between
  // sentences): jump to its English translation.
  return off < s.orig[1] - s.orig[0] ? s.mix_es[0] + off : s.mix_en[0];
}

function mixToEs(t) {
  const i = sentenceAt(t, "mix_es");
  if (i < 0) return 0;
  const s = lesson.sentences[i];
  const off = t - s.mix_es[0];
  // In the English part or the gap after it: continue Spanish after this sentence.
  return off < s.mix_es[1] - s.mix_es[0] ? s.orig[0] + off : s.orig[1];
}

// ---------- player

function src(m) { return `lessons/${encodeURIComponent(lesson.id)}/${m}.mp3`; }

function loadAt(m, t, autoplay) {
  return new Promise((resolve) => {
    audio.src = src(m);
    audio.addEventListener("loadedmetadata", () => {
      audio.playbackRate = speed;  // a new src resets the rate
      audio.currentTime = Math.min(t, audio.duration || t);
      if (autoplay) audio.play().catch(() => {});
      resolve();
    }, { once: true });
  });
}

// ---------- conversion (server converts each video once and keeps it)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let openToken = 0;  // bumps whenever a different video is opened; stale loops stop

let phase = null;
function showPhase(p) {  // "converting" | "choice" | "offline" | "player"
  phase = p;
  $("converting").hidden = p !== "converting";
  $("conv-choice").hidden = p !== "choice";
  $("offline-msg").hidden = p !== "offline";
  $("player-ui").hidden = p !== "player";
}

// Rough estimates from test runs on 5-6.5 minute videos:
//   Pi:   ~1.4x the video length, Claude Haiku translation ≈ $0.0008
//   Pi + Kokoro voice: about 1x the video length more than that
//   Fast: ~0.3x the video length, Whisper API + Claude Opus + OpenAI TTS ≈ $0.15
function estimates(v) {
  const min = v.duration / 60;
  const t = (m) => (m < 1.5 ? "about a minute" : `about ${Math.round(m)} min`);
  return {
    local: { time: t(min * 1.4), cost: 0.001 },
    kokoro: { time: t(min * 2.4), cost: 0.001 },
    cloud: { time: t(min * 0.3), cost: Math.max(0.05, min * 0.025) },
  };
}

function showChoice(v, error) {
  const e = estimates(v);
  $("choice-title").textContent = error ? "Couldn't convert this video" : "This video isn't converted yet";
  $("choice-detail").textContent = error
    ? `${error} Pick an option to try again.`
    : "Choose how to convert it. Nothing starts until you pick.";
  $("kokoro-est").textContent = `${e.kokoro.time} · free, except ≈ $0.001 for the translation`;
  $("local-est").textContent = `${e.local.time} · robotic English voice · ≈ $0.001`;
  $("cloud-est").textContent = `${e.cloud.time} · ≈ $${e.cloud.cost.toFixed(2)} in API costs`;
  showPhase("choice");
}

function chooseEngine(engine) {
  if (!video) return;
  if (engine === "cloud") {
    const cost = estimates(video).cloud.cost.toFixed(2);
    if (!confirm(`Fast conversion uses paid APIs and will cost about $${cost}. Continue?`)) return;
  }
  openPlayer(video.id, true, engine);
}

function showProgress(st) {
  const queued = st.state === "queued";
  $("conv-title").textContent = queued ? "Waiting in line…"
    : st.engine === "cloud" ? "Converting (fast)…"
    : st.engine === "local" || st.engine === "kokoro" ? "Converting on the Pi…"
    : "Preparing your audio…";
  const count = st.total ? ` (${st.done}/${st.total})` : "";
  $("conv-step").textContent = (st.message || "Starting") + count;
  const bar = $("conv-bar");
  bar.classList.toggle("indeterminate", !st.total);
  bar.style.width = st.total ? `${Math.round((100 * st.done) / st.total)}%` : "";
}

// Resolves with lesson.json once the video's audio exists, or null if it isn't
// converted (the choice screen is shown), it failed, or the user moved on.
// A conversion only starts when `engine` was explicitly chosen by the user.
async function ensureLesson(v, token, engine) {
  const q = `id=${encodeURIComponent(v.id)}`;
  let st = await (await fetch(`api/status?${q}`, { cache: "no-store" })).json();
  if (st.state === "none" || st.state === "error") {
    if (!engine) {
      if (token === openToken) showChoice(v, st.state === "error" ? (st.detail || st.message) : null);
      return null;
    }
    st = await (await fetch(`api/convert?${q}&engine=${engine}`, { method: "POST" })).json();
  }
  while (st.state === "running" || st.state === "queued") {
    if (token !== openToken) return null;
    showPhase("converting");
    showProgress(st);
    await sleep(1500);
    st = await (await fetch(`api/status?${q}`, { cache: "no-store" })).json();
  }
  if (token !== openToken) return null;
  if (st.state !== "ready") {
    showChoice(v, st.detail || st.message || "Unknown error.");
    return null;
  }
  return getJSON(`lessons/${encodeURIComponent(v.id)}/lesson.json`);
}

async function openPlayer(id, force = false, engine = null) {
  const v = videos.find((x) => x.id === id);
  if (!v) { location.hash = "#/"; return; }
  if (!force && video && video.id === v.id && lesson) return;  // already open
  saveState();
  audio.pause();
  const token = ++openToken;
  video = v;
  lesson = null;
  $("video-title").textContent = v.title;
  $("video-meta").textContent = `${fmt(v.duration)} · Difficulty ${v.difficulty} · ${v.level}`;
  $("video-thumb").src = v.thumbnail;
  showPhase("converting");
  showProgress({ message: "Checking" });
  downloadNote("");

  let l;
  try {
    // Downloaded lessons open from this device (sw.js serves them), even offline.
    l = downloaded.has(v.id) ? await getJSON(lessonUrl(v.id, "lesson.json"))
      : await ensureLesson(v, token, engine);
  } catch (e) {
    if (token !== openToken) return;
    if (navigator.onLine) showChoice(v, "Can't reach the server.");
    else showPhase("offline");
    return;
  }
  if (!l || token !== openToken) return;
  lesson = l;
  showPhase("player");
  renderDownload();
  const saved = loadState(v.id);
  mode = saved.mode || "es";
  renderMode();
  lastSentence = -2;
  await loadAt(mode, saved.t || 0, false);
  updateMediaSession();
  render();
}

async function setMode(m) {
  if (m === mode || !lesson) return;
  const playing = !audio.paused;
  const t = m === "mix" ? esToMix(audio.currentTime) : mixToEs(audio.currentTime);
  mode = m;
  renderMode();
  await loadAt(mode, t, playing);
  updateMediaSession();
  saveState();
}

// ---------- persistence (per-viewer convenience only), keyed by video

function loadState(id) {
  try { return JSON.parse(localStorage.getItem(`pos:${id}`)) || {}; } catch { return {}; }
}
function saveState() {
  if (!video || !lesson || !audio.duration) return;
  const frac = audio.currentTime / audio.duration;
  const watched = loadState(video.id).watched || frac >= WATCHED_AT;
  try { localStorage.setItem(`pos:${video.id}`, JSON.stringify({ mode, t: audio.currentTime, frac, watched })); } catch {}
}

// ---------- playback speed (one setting for all videos)

let speed = 1;
try { speed = SPEEDS.includes(+localStorage.getItem("speed")) ? +localStorage.getItem("speed") : 1; } catch {}

function renderSpeed() {
  const box = $("speed");
  box.innerHTML = "";
  for (const s of SPEEDS) {
    const b = document.createElement("button");
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", s === speed);
    b.textContent = `${s}×`;
    b.onclick = () => setSpeed(s);
    box.append(b);
  }
}
function setSpeed(s) {
  speed = s;
  audio.defaultPlaybackRate = s;
  audio.playbackRate = s;
  try { localStorage.setItem("speed", String(s)); } catch {}
  renderSpeed();
  updatePositionState();
}
audio.preservesPitch = true;
audio.defaultPlaybackRate = speed;
renderSpeed();

// ---------- controls

function togglePlay() { if (!lesson) return; audio.paused ? audio.play() : audio.pause(); }
function skip(d) {
  if (!lesson) return;
  audio.currentTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + d));
}

$("play").onclick = togglePlay;
$("back").onclick = () => skip(-SKIP);
$("fwd").onclick = () => skip(SKIP);
$("mode-es").onclick = () => setMode("es");
$("mode-mix").onclick = () => setMode("mix");
$("en-text").onclick = () => { revealEn = true; render(); };
$("engine-kokoro").onclick = () => chooseEngine("kokoro");
$("engine-local").onclick = () => chooseEngine("local");
$("engine-cloud").onclick = () => chooseEngine("cloud");

const seek = $("seek");
let seeking = false;
seek.oninput = () => { seeking = true; $("cur").textContent = fmt(+seek.value); };
seek.onchange = () => { audio.currentTime = +seek.value; seeking = false; };

$("search").oninput = renderList;
// List filters, remembered per device
for (const [box, key] of [["hide-watched", "hideWatched"], ["only-downloaded", "onlyDownloaded"]]) {
  try { $(box).checked = !!localStorage.getItem(key); } catch {}
  $(box).onchange = () => {
    try { localStorage.setItem(key, $(box).checked ? "1" : ""); } catch {}
    renderList();
  };
}

document.addEventListener("keydown", (e) => {
  if ($("player-view").hidden || e.target.tagName === "INPUT") return;
  if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  if (e.code === "ArrowLeft") skip(-SKIP);
  if (e.code === "ArrowRight") skip(SKIP);
});

// ---------- rendering

function renderMode() {
  $("mode-es").setAttribute("aria-checked", mode === "es");
  $("mode-mix").setAttribute("aria-checked", mode === "mix");
}

let lastSentence = -2;
function render() {
  if (!lesson) return;
  const t = audio.currentTime;
  const i = sentenceAt(t, mode === "es" ? "orig" : "mix_es");
  if (i !== lastSentence) { lastSentence = i; revealEn = false; }
  const s = lesson.sentences[i];
  $("es-text").textContent = s ? s.es : "…";
  const en = $("en-text");
  en.textContent = s ? s.en : "";
  // In Spanish-only mode the translation is blurred until tapped.
  en.classList.toggle("hidden", mode === "es" && !revealEn);

  seek.max = audio.duration || 0;
  if (!seeking) { seek.value = t; $("cur").textContent = fmt(t); }
  $("dur").textContent = fmt(audio.duration);
}

audio.addEventListener("timeupdate", () => { render(); updatePositionState(); });
audio.addEventListener("durationchange", render);
audio.addEventListener("play", () => { $("play").classList.add("playing"); $("play").ariaLabel = "Pause"; });
audio.addEventListener("pause", () => { $("play").classList.remove("playing"); $("play").ariaLabel = "Play"; saveState(); });
audio.addEventListener("ended", saveState);
setInterval(() => { if (!audio.paused) saveState(); }, 5000);

// ---------- lock screen / headphone controls

function updateMediaSession() {
  if (!("mediaSession" in navigator) || !video) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: video.title,
    artist: mode === "es" ? "Español" : "Español + English",
    artwork: [{ src: video.thumbnail, sizes: "640x360", type: "image/jpeg" }],
  });
}
function updatePositionState() {
  if (!("mediaSession" in navigator) || !audio.duration) return;
  try {
    navigator.mediaSession.setPositionState({
      duration: audio.duration, position: Math.min(audio.currentTime, audio.duration), playbackRate: audio.playbackRate,
    });
  } catch {}
}
if ("mediaSession" in navigator) {
  const ms = navigator.mediaSession;
  ms.setActionHandler("play", () => audio.play());
  ms.setActionHandler("pause", () => audio.pause());
  ms.setActionHandler("seekbackward", () => skip(-SKIP));
  ms.setActionHandler("seekforward", () => skip(SKIP));
  try { ms.setActionHandler("seekto", (d) => { audio.currentTime = d.seekTime; }); } catch {}
}

// ---------- routing

function route() {
  const m = location.hash.match(/^#\/play\/(.+)$/);
  const playing = !!m;
  $("list-view").hidden = playing;
  $("player-view").hidden = !playing;
  window.scrollTo(0, 0);
  if (playing) openPlayer(decodeURIComponent(m[1]));
  else { audio.pause(); saveState(); openToken++; video = null; lesson = null; renderList(); }
}
window.addEventListener("hashchange", route);
window.addEventListener("online", () => {
  renderList();
  showStorage();
  if (video && phase === "offline") openPlayer(video.id, true);  // load what the banner was waiting for
});
window.addEventListener("offline", () => {
  renderList();
  // Converting needs the Pi: swap the choice buttons for the banner.
  if (video && phase === "choice") showPhase("offline");
});

async function init() {
  try { videos = await getJSON("data/videos.json"); } catch { videos = []; }
  await loadDownloaded();
  showStorage();
  renderList();
  route();
}
init();

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
