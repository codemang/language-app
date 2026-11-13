// Escucha — browse Dreaming Spanish videos, play them as Spanish-only or
// Spanish/English interleaved audio.
//
// Routes:  #/            video list (data/videos.json)
//          #/play/<id>   player
//
// For now every video plays the same static lesson (the first entry in
// lessons/index.json). A lesson has es.mp3, mix.mp3 and lesson.json with
// per-sentence timings in both: orig = [start, end] in es.mp3, mix_es / mix_en =
// where the Spanish clip and its English translation sit in mix.mp3. Switching
// modes maps the position through those timings so you stay on the same sentence.

const $ = (id) => document.getElementById(id);
const audio = $("audio");
const SKIP = 5;

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

function renderList() {
  const ul = $("videos");
  ul.innerHTML = "";
  $("list-empty").hidden = videos.length > 0;
  for (const v of videos) {
    const li = document.createElement("li");
    li.innerHTML = `
      <a class="card" href="#/play/${encodeURIComponent(v.id)}">
        <div class="thumb">
          <img loading="lazy" alt="">
          <span class="badge">${fmt(v.duration)}</span>
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
      audio.currentTime = Math.min(t, audio.duration || t);
      if (autoplay) audio.play().catch(() => {});
      resolve();
    }, { once: true });
  });
}

// ---------- conversion (server converts each video once and keeps it)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let openToken = 0;  // bumps whenever a different video is opened; stale loops stop

function showPhase(phase) {  // "converting" | "choice" | "player"
  $("converting").hidden = phase !== "converting";
  $("conv-choice").hidden = phase !== "choice";
  $("player-ui").hidden = phase !== "player";
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

  let l;
  try { l = await ensureLesson(v, token, engine); } catch (e) {
    if (token !== openToken) return;
    showChoice(v, "Can't reach the server.");
    return;
  }
  if (!l || token !== openToken) return;
  lesson = l;
  showPhase("player");
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
  if (!video || !lesson) return;
  try { localStorage.setItem(`pos:${video.id}`, JSON.stringify({ mode, t: audio.currentTime })); } catch {}
}

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
  else { audio.pause(); openToken++; video = null; lesson = null; }
}
window.addEventListener("hashchange", route);

async function init() {
  try { videos = await getJSON("data/videos.json"); } catch { videos = []; }
  renderList();
  route();
}
init();

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
