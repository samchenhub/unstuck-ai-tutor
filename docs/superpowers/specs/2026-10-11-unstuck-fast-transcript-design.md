# Unstuck — ask sooner on long videos without captions

Status: approved by the user 2026-10-11 ("可以，先1，2一起吧。3先不做吧，可以备注一下。因为肯定有很多windows用户会使用的。")

## Problem

A video with no caption track (e.g. a 1 h 45 min English lecture whose subtitles are burned into the picture) can only be asked about after the whole video is downloaded and the whole audio is transcribed. Measured on the user's M4: the 216 MB MP4 took 5 min to download (CDN at ~0.7 MB/s that hour) and faster-whisper `small` runs at ~10× real time on CPU, so asking would open after ~15–20 min.

## Design

### 1. Audio-only download for transcription (Bilibili)
- `BiliClient.playurl_dash(bvid, cid)`: WBI-signed `x/player/wbi/playurl` with `fnval=16` (DASH).
- `parse.dash_audio_url(data)`: the lowest-bandwidth `dash.audio[]` entry's `baseUrl` (https-normalised). Speech needs no more; the 1 h 45 min lecture is 52 MB (vs 216 MB for the 720p MP4).
- `BilibiliSource.download_audio` → `out_dir/"audio.m4a"` through the existing ranged downloader (`download_ranges`, which refreshes the URL by calling the DASH lookup again). DASH needs the bilibili Referer, which the backend already sends (`HEADERS`). If `video.mp4` already exists it is reused; if the response has no DASH audio, fall back to downloading the MP4.
- The full MP4 is still downloaded in the `download` stage for keyframes, after asking has opened.
- YouTube already downloads audio only (`bestaudio`).

### 2. Progressive transcription
- `transcribe.iter_transcript(path, model_name, *, start=0.0, chunk_s=600)` decodes the audio with ffmpeg one 10-minute chunk at a time (`-ss/-t`, bounded memory instead of the whole file as float32), detects the language once on the first chunk (VAD), keeps the simplified-Chinese prompt for zh, and yields `TranscriptProgress(cues, covered_until)`: one event per Whisper segment (absolute times) and one at each chunk end. It stops at the first chunk with no audio.
- `stage_transcript` (no-captions path) accumulates cues and publishes them — `replace_cues`, rebuild the transcript-only timeline, rebuild the index, `ask_ready=True`, `transcribed_until=covered` — first once 60 s of audio is covered, then every 300 s, and at the end. Asking opens after the first publish.
- Resume: the new `videos.transcribed_until` column (REAL, default 0) records coverage. If the transcript stage restarts (app restarted mid-import, or retry), it keeps the stored cues that start before `transcribed_until` and continues from there. Caption-based transcripts set it to 0.
- Tutor: while the transcript stage is not done and the pause point is past `transcribed_until`, the context says the transcript there is still being generated (covers up to [mm:ss]) so the model leans on the live frame and says so.
- Frontend: the transcript stage chip shows `已转写 mm:ss` while running; the headline already says asking is open once `ask_ready` is set.

### Deferred
- **Apple-GPU transcription (mlx-whisper)**: several times faster on Apple Silicon but Mac-only; many users will be on Windows, so the cross-platform faster-whisper path stays the default. Revisit only as an optional accelerator with the CPU fallback.
- Transcribing while the audio is still downloading.

## Testing
Unit tests with fakes (no network, no real Whisper): DASH audio pick, audio download path/reuse/fallback, chunked iteration (offsets, language detected once, stop on empty chunk, resume start), publish cadence + resume in the stage, tutor note, frontend label helper. Real check: transcribe the user's downloaded lecture's first minutes and time to `ask_ready` on a fresh Bilibili import without captions.
