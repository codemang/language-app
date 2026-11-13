# Storage: options for keeping audio off the Pi (not started)

Status: findings only, nothing implemented. Today all audio lives on the Pi's SD card,
capped at 3 GB (see `storage.py`).

## How much space audio takes

Measured on Nov 13, 2026, with MP3s at 96 kbps mono:

| | Per minute of video |
|---|---|
| Finished lesson (`web/lessons/<id>`): `es.mp3` + `mix.mp3` + JSON | ~1.8 MB |
| Conversion cache (`work/<id>`): download, transcripts, English speech | ~2.3 MB |
| Peak while converting (adds the decoded WAV + cut clips, deleted afterwards) | +5.5 MB |

- Each minute of video gives ~2.5 minutes of audio: 1 min Spanish-only, plus ~1.5 min mixed.
- 10 GB is ~13,900 minutes of audio, or ~90 hours of video.
- The 10 beginner videos in the app come to ~95 minutes of video.
- Ways to fit more if ever needed:
  - 64 kbps MP3s: ~1/3 more.
  - Dropping `es.mp3` and playing `mix.mp3` with the English skipped: ~60% more.

## Options

1. **USB stick / SSD on the Pi.** Simplest: move the lesson folder onto it, nothing else
   changes. It also spares the SD card wear. About 10 minutes of setup.
2. **Network share (NAS / always-on PC).** The Pi mounts it over SMB/NFS. Only works while
   that machine is on; reachable away from home over Tailscale.
3. **Cloudflare R2** (or Backblaze B2). Free tier ~10 GB, and R2 has no download fees.
   The Pi uploads each lesson after it's built and deletes its own copy; the app streams
   from R2.
   - The bucket must be **private**, since it holds Dreaming Spanish audio. No public
     URLs, nothing indexable.
   - The Pi signs **presigned URLs** (e.g. 12 h) with an R2 key scoped to the bucket. The
     phone streams directly from Cloudflare, and Range requests mean seeking works.
   - The Pi is still needed to open a lesson, because it hands out the links. Playback
     already in progress survives the Pi being offline.
   - Expired links: the app must catch the failed request, get a new link and resume
     at the same position.
   - Every link is a different URL, so offline caching must key files by lesson id.
   - Needs a Cloudflare account with R2 enabled, which I believe requires a payment
     method on file even for the free tier.
   - Variant without the Pi: a custom domain plus Cloudflare Access (free for small use),
     logging in once with an email code. Needs a Cloudflare-managed domain, and the lesson
     list would also live in R2.
4. **Download to the phone.** A per-lesson Download button stores the MP3s in the PWA
   (Cache Storage). Works offline, and the Pi can then evict that lesson.
   - Risks: the phone may evict that storage (iOS especially, after weeks of non-use),
     and it only covers one device.
5. **Store almost nothing.** Keep only the English clips (~0.7 MB/min) and stream the
   Spanish audio from Dreaming Spanish on every play. Not recommended: it hits their CDN
   on every listen, depends on the login token, and makes playback more fragile.

## Recommendation

- If the goal is more room on the Pi: option 1 (USB drive).
- If the goal is listening without the Pi: option 4, which can be combined with any of the others.
- Option 3 (R2) is worth it if the lessons should live off the Pi entirely.
