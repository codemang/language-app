// Caches the app shell so it opens instantly / installs as a PWA, and serves
// lessons the user downloaded for offline listening.
//
// Downloaded lessons live in OFFLINE (written by app.js, never cleared on update)
// and are always served from there, even online, so the audio and its sentence
// timings stay a matching set. Audio is fetched with Range requests (iOS insists
// on 206 responses for media), so ranges are cut from the cached file here.
// Lessons that aren't downloaded come straight from the network.
const CACHE = "escucha-v31";
const OFFLINE = "escucha-offline";
const VERBS = "escucha-verbs";    // verb drills saved with "Download all" (verbs.js)
const NET_WAIT = 3000;  // ms: longer than this, a cached copy is used instead
const SHELL = ["./", "index.html", "style.css", "app.js", "verbs.js", "manifest.webmanifest",
               "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== OFFLINE && k !== VERBS).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function fromOffline(request) {
  const cache = await caches.open(OFFLINE);
  const hit = await cache.match(request.url, { ignoreSearch: true });
  if (!hit) return fetch(request);
  const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.get("Range") || "");
  if (!range) return hit;
  const blob = await hit.blob();
  const size = blob.size;
  let start, end;
  if (range[1] === "") { start = Math.max(0, size - Number(range[2])); end = size - 1; }  // last N bytes
  else { start = Number(range[1]); end = range[2] ? Math.min(Number(range[2]), size - 1) : size - 1; }
  if (start >= size || start > end) {
    return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
  }
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    headers: {
      "Content-Type": hit.headers.get("Content-Type") || "audio/mpeg",
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Content-Length": String(end - start + 1),
      "Accept-Ranges": "bytes",
    },
  });
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.includes("/api/")) return;
  if (url.pathname.includes("/lessons/")) {
    e.respondWith(fromOffline(e.request));
    return;
  }
  if (url.searchParams.has("dl")) return;  // a download: always the network
  // Verb clips are named by a hash of their content, so a saved copy is never stale.
  if (url.pathname.includes("/verbs/clips/")) {
    e.respondWith(caches.match(e.request, { ignoreSearch: true }).then((hit) => hit || fetch(e.request)));
    return;
  }
  // App shell, video list, thumbnails, verb data: network first so updates show
  // up, but on a weak connection don't wait more than NET_WAIT for it: use the
  // cached copy (the network answer still refreshes the cache for next time).
  const network = fetch(e.request).then((res) => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
    }
    return res;
  });
  e.waitUntil(network.catch(() => {}));
  e.respondWith((async () => {
    const slow = new Promise((r) => setTimeout(() => r("slow"), NET_WAIT));
    const first = await Promise.race([network.catch(() => null), slow]);
    if (first && first !== "slow") return first;
    const hit = await caches.match(e.request, { ignoreSearch: true });
    return hit || network;  // nothing cached: keep waiting (or fail) on the network
  })());
});
