# Unstuck

> *Stuck on a video? Pause and ask.*

Unstuck is a one-on-one AI tutor that sits next to a YouTube lecture. Pause anywhere and ask a question: the tutor knows **which second you are at** and **what is on screen** (formulas, code, diagrams, slides), explains with the video's own content, and answers with clickable `[mm:ss]` timestamps and rendered LaTeX.

Products I looked at only read the transcript; research systems understand frames but don't know where the viewer is. Unstuck combines both at the pause point.

## How it works

```
YouTube URL
  └─ import pipeline (resumable, 8 stages)
       metadata → transcript → download → keyframes → vision → timeline → index → outline
         │            │                      │           │         │          │
         │            └─ captions or Whisper │           │         │          └─ sqlite-vec (bge-m3)
         │                                   │           │         └─ 30–60 s segments cut at sentence ends / scene changes
         │                                   │           └─ GLM vision: kind, summary, OCR (LaTeX/code), change note
         │                                   └─ scene detection (short scenes merged, scenes over 45 s split), thinned to ≤ ~1 frame / 10 s (max 300), phash dedupe
         └─ you can start asking as soon as the transcript is in

Question at second t
  └─ context (~6k tokens):
       ① outline + current chapter   ② transcript t−90 s … t+15 s
       ③ current screen (live ffmpeg grab + vision if the last keyframe is > 15 s old)
       ④ top-3 retrieved segments ("covered later" if after t)   ⑤ last 6 rounds
     → streamed answer (SSE) with [mm:ss] seek links and KaTeX
```

Design details: `docs/superpowers/specs/2026-10-09-unstuck-design.md`.

## Stack

FastAPI · SQLite + sqlite-vec · yt-dlp · ffmpeg · PySceneDetect · faster-whisper · imagehash · React + TypeScript + Vite · KaTeX. Models go through one interface (`backend/unstuck/llm`); the defaults are Zhipu's free `glm-4.7-flash` (text) and `glm-4.6v-flash` (vision) plus local `BAAI/bge-m3` embeddings. Any OpenAI-compatible endpoint works by changing `.env`. Model calls retry with backoff (2/4/8/16/32 s) to ride out free-tier rate limits. The tutor answers in the language of the question.

## Run it locally

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 20.19+ (required by Vite 8), ffmpeg (`brew install uv node ffmpeg`), and a Zhipu API key from open.bigmodel.cn.

```bash
cd backend
cp .env.example .env        # put your key in LLM_API_KEY
uv sync
uv run uvicorn unstuck.api.app:create_app --factory --port 8000
```

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173
```

The backend loads the bge-m3 embedding model in a background thread at startup, so the first import does not pay for it (the first run downloads ~2 GB). Videos without captions also download a Whisper model (`WHISPER_MODEL`, default `small`).

If the free-tier model stays rate-limited after the retries, the stage ends with `MODEL_BUSY`; during frame understanding it stops at once and the frames not yet described stay pending. `VISION_PARTIAL` now only means some frame descriptions failed for other reasons. Press **重试** (retry) in the UI: finished stages are skipped and only the unfinished or failed parts re-run, for example just the frames that are still pending or failed vision.

## Tests

```bash
cd backend && uv run pytest -q                               # unit tests, no network
cd backend && UNSTUCK_IT_URL="<short CC video>" uv run pytest -m slow -s   # real end-to-end
cd frontend && npm test
```

## Evaluation

`eval/` compares answers from the transcript alone against transcript + screen on hand-written questions. The eval needs a hand-written `eval/questions.json` (format in `eval/questions.example.json`; see `eval/README.md`). Reports are written to `eval/results/`.

## Limits

- yt-dlp downloads are for personal use and demos. YouTube often blocks server IPs, so Unstuck runs locally only.
- Free vision models can misread complex formulas; switch `VISION_MODEL` to a stronger model if needed.
- Videos longer than 2 hours are rejected.
- On macOS you may see an `objc ... AVFFrameReceiver is implemented in both` warning at start-up. opencv and PyAV each bundle FFmpeg; it is harmless because Unstuck never opens capture devices.
