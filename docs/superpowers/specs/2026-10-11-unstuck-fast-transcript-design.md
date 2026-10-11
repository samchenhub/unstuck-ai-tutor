# Unstuck — ask sooner on long videos without captions

Status: approved by the user 2026-10-11 ("可以，先1，2一起吧。3先不做吧，可以备注一下。因为肯定有很多windows用户会使用的。")

## Problem

A video with no caption track (e.g. a 1 h 45 min English lecture whose subtitles are burned into the picture) can only be asked about after the whole video is downloaded and the whole audio is transcribed. Measured on the user's M4: the 216 MB MP4 took 5 min to download (CDN at ~0.7 MB/s that hour) and faster-whisper `small` runs at ~10× real time on CPU, so asking would open after ~15–20 min.

## Design

### 1. Audio-only download for transcription (Bilibili)
- `BiliClient.playurl_dash(bvid, cid)`: WBI-signed `x/player/wbi/playurl` with `fnval=16` (DASH).
- `parse.dash_audio_urls(data)`: the lowest-bandwidth `dash.audio[]` entry's `baseUrl` followed by its `backupUrl` mirrors (https-normalised); the downloader probes the mirrors and uses the fastest (speeds differ up to ~10×). Speech needs no more; the 1 h 45 min lecture is 52 MB (vs 216 MB for the 720p MP4).
- `BilibiliSource.download_audio` → `out_dir/"audio.m4a"` through the existing ranged downloader (`download_ranges`, which refreshes the URL by calling the DASH lookup again). DASH needs the bilibili Referer, which the backend already sends (`HEADERS`). If `video.mp4` already exists it is reused; any DASH failure (none offered, lookup or download failed) falls back to downloading the MP4. `audio.m4a` is deleted once the MP4 has been downloaded.
- The full MP4 is still downloaded in the `download` stage for keyframes, after asking has opened.
- YouTube already downloads audio only (`bestaudio`).

### 2. Progressive transcription
- `transcribe.iter_transcript(path, model_name, *, start=0.0, chunk_s=600)` decodes the audio with ffmpeg one 10-minute chunk at a time (`-ss/-t`, bounded memory instead of the whole file as float32), detects the language on the first chunk with speech (VAD; only fixed once the probability is ≥ 0.5, since silence is detected as English at ~0.39), keeps the simplified-Chinese prompt for zh, and yields `TranscriptProgress(cues, covered_until)`: one event per Whisper segment (absolute times) and one at each chunk end. It stops at the first chunk with no audio.
- `stage_transcript` (no-captions path) accumulates cues and publishes them — embed only segments whose text is new, swap segments + vectors in one transaction (questions never see an empty index), `replace_cues`, `ask_ready=True`, `transcribed_until=covered` — first once 60 s of audio is covered, then every 300 s, and at the end. Asking opens after the first publish.
- Resume: the new `videos.transcribed_until` column (REAL, default 0) records coverage. If the transcript stage restarts (app restarted mid-import, or retry), it keeps the stored cues that start before `transcribed_until` and continues from there. Caption-based transcripts set it to 0.
- Tutor: while the transcript stage is not done and the pause point is past `transcribed_until`, the context says the transcript there is still being generated (covers up to [mm:ss]) so the model leans on the live frame and says so.
- Frontend: the transcript stage chip shows `已转写 mm:ss` while running; the headline already says asking is open once `ask_ready` is set.

### Deferred
- **Apple-GPU transcription (mlx-whisper)**: several times faster on Apple Silicon but Mac-only; many users will be on Windows, so the cross-platform faster-whisper path stays the default. Revisit only as an optional accelerator with the CPU fallback.
- Transcribing while the audio is still downloading.
- Overlap between 10-minute chunks (a word spanning a cut can be lost), probing mirrors outside the refresh lock, capping the probe read (review minors, accepted).

## Testing
Unit tests with fakes (no network, no real Whisper): DASH audio pick, audio download path/reuse/fallback, chunked iteration (offsets, language detected once, stop on empty chunk, resume start), publish cadence + resume in the stage, tutor note, frontend label helper. Real check: transcribe the user's downloaded lecture's first minutes and time to `ask_ready` on a fresh Bilibili import without captions.

## Round 2 (approved 2026-10-11: "可以，1、2、3和画面理解重试一起做吧")

Measured after round 1: 55 s to ask on the 1 h 45 min lecture = ~40 s audio download + ~10 s first Whisper minute + a few seconds metadata/index. The mirror probe (512 KB) is latency-dominated and picked a 1.3 MB/s mirror while another served 12 MB/s.

1. **Transcribe while downloading.** `download_ranges(..., on_progress=cb)` reports the contiguous prefix written (`cb(prefix_bytes, total_bytes)`; chunks are queued in order). `sources.base.AudioFeed` is the hand-off: the producer (a download thread) calls `progress/finish/fail`; the consumer (`iter_transcript`) calls `wait_for(seconds)` before decoding a chunk. Readable seconds ≈ `(prefix − 256 KB) / total × duration × 0.97`; if a decode comes back short while the download is still running, it waits for more data and decodes again (never mistakes a partial file for the end). `AudioFeed.complete(path)` wraps a finished file (YouTube, reused files, MP4 fallback). `Source.open_audio(video_id, out_dir, duration) -> AudioFeed` replaces `download_audio` in the transcript stage; Bilibili starts the DASH download in a thread, and if it fails mid-way the same thread falls back to the MP4 and completes the feed with it.
2. **Use all mirrors at once.** Instead of racing a probe, each download worker is pinned to a mirror (round-robin), so faster mirrors finish more chunks from the shared in-order queue. A mirror with `attempts_per_url` consecutive failures is set aside; when all are set aside (or on 403) the URL list is refreshed (existing budget). Chunk 0 (which learns the size) is raced across mirrors and the first complete response is written.
3. **First chunk 2 minutes.** `iter_transcript(first_chunk_s=120)`; later chunks stay 10 minutes.
4. **Automatic retry when the free vision model is rate-limited.** `Pipeline.run` returns whether a stage failed with `ModelBusyError`. The runner then schedules a re-run after 2, 5, 10, 20, 30 minutes (5 attempts), stored in new columns `videos.auto_retry_at` (epoch seconds, 0 = none) and `auto_retries`, and re-scheduled on startup. A re-run resumes from the failed stage; frames already described are kept. The counter resets when a run finishes without a rate limit; after the last attempt the message asks for a manual retry. Frontend: keeps polling while a retry is scheduled, shows the vision stage as `等待重试` and the time of the next attempt.
