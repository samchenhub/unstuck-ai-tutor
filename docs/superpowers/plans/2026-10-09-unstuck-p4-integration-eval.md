# Unstuck Part 4 — Integration Test, Answer-Quality Eval & README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the whole system works on a real video (slow integration test), measure how much seeing the screen improves answers (transcript-only vs transcript + frames eval), and document the project for portfolio readers.

**Architecture:** The integration test runs the real `Pipeline` and `stream_answer` against a YouTube URL from an environment variable and is excluded from the default test run (`slow` marker). The eval logic (dataset loading, judge prompt, verdict parsing, scoring, Markdown report) lives in `unstuck/evaluation.py` so it is unit-tested with `FakeLLM`; `eval/run_eval.py` is a thin CLI that runs it against imported videos and writes `eval/results/report-<date>.md`.

**Tech Stack:** pytest (slow marker), the Part 1–2 backend, Markdown report output.

Part 4 of 4. Requires Parts 1–3 complete. Spec §8 (testing strategy) and §10 (known limits).

## Global Constraints

- Unit tests never call external APIs; the integration test is marked `slow` and is not part of the default run.
- Integration video: 2–3 minutes long, licensed for reuse (e.g. Creative Commons), set via `UNSTUCK_IT_URL`; no URL is hard-coded.
- Eval: 3 videos, ~20 hand-written items of "time + question + key points", each answered in two modes (`transcript_only`, `full`), scored by an LLM judge on key-point coverage, written up as a comparison report.
- Only borrow ideas from reference projects; never copy code.
- yt-dlp downloads are for personal/demo use; the project runs locally only.

## File Structure (Part 4)

```
backend/tests/test_integration.py   slow end-to-end test (real network + models)
backend/unstuck/evaluation.py        EvalItem, judge prompt/parsing, run_eval, render_report
backend/tests/test_evaluation.py     unit tests with FakeLLM
eval/
  README.md                          methodology + how to run
  questions.example.json             dataset format
  questions.json                     the real dataset (written by hand, Task 21 Step 6)
  run_eval.py                        CLI
  results/report-YYYY-MM-DD.md       generated report (committed for the portfolio)
README.md                            project overview, architecture, setup, usage
```

---

### Task 20: Slow end-to-end integration test

**Files:**
- Create: `backend/tests/test_integration.py`

**Interfaces:**
- Consumes: `load_settings`, `build_llm`, `connect`, `repo`, `parse_video_id`, `watch_url`, `Pipeline`, `StageContext`, `stream_answer`.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the test**

`backend/tests/test_integration.py`:

```python
"""Full import + answer against a real YouTube video and the real models.

Run: cd backend && UNSTUCK_IT_URL=<url of a 2-3 min Creative Commons video> uv run pytest -m slow -s
Needs network, ffmpeg and backend/.env with LLM_API_KEY. Takes a few minutes.
"""

import os
from dataclasses import replace

import pytest

from unstuck.config import load_settings
from unstuck.db import connect, repo
from unstuck.ingest.pipeline import Pipeline, StageContext
from unstuck.ingest.youtube import parse_video_id, watch_url
from unstuck.llm.factory import build_llm
from unstuck.tutor.answer import stream_answer

URL = os.getenv("UNSTUCK_IT_URL")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not URL, reason="set UNSTUCK_IT_URL to a short Creative Commons YouTube video"),
]


def test_import_and_answer_real_video(tmp_path):
    settings = replace(load_settings(), data_dir=tmp_path)
    llm = build_llm(settings)
    conn = connect(settings.db_path, settings.embed_dim)
    video_id = parse_video_id(URL)
    repo.insert_video(conn, video_id, watch_url(video_id))

    Pipeline().run(StageContext(conn=conn, llm=llm, settings=settings, video_id=video_id))

    video = repo.get_video(conn, video_id)
    print("stage states:", video.stage_states, "error:", video.error_code, video.error_message)
    assert video.status == "ready"
    assert video.ask_ready
    assert repo.get_segments(conn, video_id)
    assert video.outline
    if video.visual_mode == "full":
        frames = repo.get_keyframes(conn, video_id)
        assert frames
        assert any(f.status == "ok" for f in frames)

    t = min(60.0, video.duration / 2)
    events = list(stream_answer(conn, llm, settings, video_id, "What is being explained right now?", t))
    answer = "".join(e["text"] for e in events if e["type"] == "delta")
    print("answer:", answer)
    assert events[-1]["type"] == "done"
    assert answer.strip()
```

- [ ] **Step 2: Confirm it is excluded by default**

Run: `cd backend && uv run pytest -v`
Expected: all unit tests PASS; `test_integration.py` is deselected (it does not appear as run).

- [ ] **Step 3: Run it for real**

Pick a 2–3 minute YouTube video with a Creative Commons license (YouTube search → Filters → Creative Commons) that shows slides, formulas or code.

Run: `cd backend && UNSTUCK_IT_URL="<url>" uv run pytest -m slow -s`
Expected: PASS within a few minutes; the printed stage states are all `done` (or `skipped` for keyframes/vision if the download was blocked), and the printed answer describes the video. If it fails, the printed `error_code` / `error_message` show which stage broke. Fix it before continuing.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_integration.py
git commit -m "test: slow end-to-end import and answer against a real video"
```

---

### Task 21: Answer-quality evaluation (transcript-only vs transcript + frames)

**Files:**
- Create: `backend/unstuck/evaluation.py`, `backend/tests/test_evaluation.py`
- Create: `eval/README.md`, `eval/questions.example.json`, `eval/run_eval.py`
- Create (by hand, Step 6): `eval/questions.json`; generated: `eval/results/report-YYYY-MM-DD.md`

**Interfaces:**
- Consumes: `prepare_messages(conn, llm, settings, video_id, question, t, *, use_visual, history)`, `loads_lenient`, `parse_ts`, `fmt_ts`, `seeded_video` fixture.
- Produces:
  - `MODES = ("transcript_only", "full")`
  - `EvalItem(id: str, video_id: str, t: float, question: str, key_points: list[str])`
  - `ItemResult(item: EvalItem, mode: str, answer: str, covered: list[bool])` with property `score: float`
  - `load_items(path: Path) -> list[EvalItem]` (`t` may be seconds or `"mm:ss"`)
  - `build_judge_messages(item, answer) -> list[dict]`, `parse_judgement(raw: str, n_points: int) -> list[bool]`
  - `run_eval(conn, llm, settings, items, modes=MODES) -> list[ItemResult]`
  - `summarize(results) -> dict[str, float]` (mean score per mode, in first-seen order)
  - `render_report(results, model_name: str) -> str`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_evaluation.py`:

```python
import json

from unstuck.evaluation import (
    EvalItem,
    ItemResult,
    load_items,
    parse_judgement,
    render_report,
    run_eval,
    summarize,
)
from unstuck.llm import FakeLLM


def item(id="q1", key_points=("uses cosine", "mentions angle")):
    return EvalItem(id=id, video_id="abcdefghijk", t=110.0, question="Why cosine?", key_points=list(key_points))


def test_load_items_accepts_mmss(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([
        {"id": "a", "video_id": "abcdefghijk", "t": "01:50", "question": "q", "key_points": ["k"]},
        {"id": "b", "video_id": "abcdefghijk", "t": 5, "question": "q2", "key_points": ["k1", "k2"]},
    ]))
    items = load_items(path)
    assert [(i.id, i.t) for i in items] == [("a", 110.0), ("b", 5.0)]
    assert items[1].key_points == ["k1", "k2"]


def test_parse_judgement_variants():
    assert parse_judgement('{"covered": [true, false]}', 2) == [True, False]
    assert parse_judgement('```json\n{"covered": ["yes", "no"]}\n```', 2) == [True, False]
    assert parse_judgement('{"covered": [true]}', 3) == [True, False, False]
    assert parse_judgement('{"covered": [true, true, true]}', 2) == [True, True]
    assert parse_judgement("I think it is fine", 2) == [False, False]


def test_score_and_summary():
    results = [
        ItemResult(item(), "transcript_only", "a", [True, False]),
        ItemResult(item(), "full", "b", [True, True]),
        ItemResult(item("q2"), "transcript_only", "c", [False, False]),
        ItemResult(item("q2"), "full", "d", [True, False]),
    ]
    assert results[0].score == 0.5
    assert summarize(results) == {"transcript_only": 0.25, "full": 0.75}


def test_render_report():
    results = [
        ItemResult(item(), "transcript_only", "answer A", [True, False]),
        ItemResult(item(), "full", "answer B", [True, True]),
    ]
    report = render_report(results, model_name="glm-test")
    assert "glm-test" in report
    assert "| transcript_only | 50% |" in report
    assert "| full | 100% |" in report
    assert "| q1 | 01:50 | Why cosine? | 50% | 100% |" in report
    assert "answer B" in report


def test_run_eval_answers_and_judges_each_mode(conn, settings, seeded_video):
    llm = FakeLLM(
        chat_responses=[
            "transcript answer", '{"covered": [true, false]}',
            "visual answer", '{"covered": [true, true]}',
        ],
        embed_dim=settings.embed_dim,
    )

    results = run_eval(conn, llm, settings, [item()])

    assert [(r.mode, r.answer, r.covered) for r in results] == [
        ("transcript_only", "transcript answer", [True, False]),
        ("full", "visual answer", [True, True]),
    ]
    chat_calls = [args for name, args in llm.calls if name == "chat"]
    assert "Not available" in chat_calls[0][-1]["content"]
    assert "Slide showing the dot product formula" in chat_calls[2][-1]["content"]
    assert "transcript answer" in chat_calls[1][-1]["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_evaluation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'unstuck.evaluation'`.

- [ ] **Step 3: Implement**

`backend/unstuck/evaluation.py`:

```python
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from unstuck.config import Settings
from unstuck.jsonfix import loads_lenient
from unstuck.llm.base import LLM
from unstuck.timefmt import fmt_ts, parse_ts
from unstuck.tutor.answer import prepare_messages

MODES = ("transcript_only", "full")

JUDGE_PROMPT = """You are grading a tutor's answer against a list of key points.
For each key point decide whether the answer clearly covers it. Paraphrases count; contradictions and vague mentions do not.
Return ONLY JSON: {{"covered": [true or false for each key point, in order]}}

Question: {question}

Key points:
{points}

Answer:
{answer}"""


@dataclass(frozen=True)
class EvalItem:
    id: str
    video_id: str
    t: float
    question: str
    key_points: list[str]


@dataclass(frozen=True)
class ItemResult:
    item: EvalItem
    mode: str
    answer: str
    covered: list[bool]

    @property
    def score(self) -> float:
        return sum(self.covered) / len(self.covered) if self.covered else 0.0


def load_items(path: Path) -> list[EvalItem]:
    items = []
    for entry in json.loads(path.read_text(encoding="utf-8")):
        t = entry["t"]
        items.append(EvalItem(
            id=str(entry["id"]),
            video_id=entry["video_id"],
            t=parse_ts(t) if isinstance(t, str) else float(t),
            question=entry["question"],
            key_points=list(entry["key_points"]),
        ))
    return items


def build_judge_messages(item: EvalItem, answer: str) -> list[dict]:
    points = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(item.key_points))
    return [{"role": "user", "content": JUDGE_PROMPT.format(question=item.question, points=points, answer=answer)}]


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes")
    return value is True


def parse_judgement(raw: str, n_points: int) -> list[bool]:
    obj = loads_lenient(raw)
    covered = obj.get("covered") if isinstance(obj, dict) else obj
    if not isinstance(covered, list):
        return [False] * n_points
    flags = [_truthy(v) for v in covered[:n_points]]
    return flags + [False] * (n_points - len(flags))


def run_eval(
    conn: sqlite3.Connection, llm: LLM, settings: Settings, items: list[EvalItem], modes=MODES
) -> list[ItemResult]:
    results = []
    for item in items:
        for mode in modes:
            messages = prepare_messages(
                conn, llm, settings, item.video_id, item.question, item.t,
                use_visual=(mode == "full"), history=[],
            )
            answer = llm.chat(messages)
            verdict = llm.chat(build_judge_messages(item, answer), json_mode=True)
            results.append(ItemResult(item, mode, answer, parse_judgement(verdict, len(item.key_points))))
    return results


def summarize(results: list[ItemResult]) -> dict[str, float]:
    by_mode: dict[str, list[float]] = {}
    for result in results:
        by_mode.setdefault(result.mode, []).append(result.score)
    return {mode: sum(scores) / len(scores) for mode, scores in by_mode.items()}


def render_report(results: list[ItemResult], model_name: str) -> str:
    summary = summarize(results)
    modes = list(summary)
    by_item: dict[str, dict[str, ItemResult]] = {}
    for result in results:
        by_item.setdefault(result.item.id, {})[result.mode] = result

    lines = [
        "# Unstuck answer-quality eval",
        "",
        f"Answer and judge model: `{model_name}`. "
        "Score = share of hand-written key points the answer covers, judged by the LLM.",
        "",
        "| Mode | Mean score |",
        "|---|---|",
        *[f"| {mode} | {score:.0%} |" for mode, score in summary.items()],
        "",
        "## Per question",
        "",
        "| ID | Time | Question | " + " | ".join(modes) + " |",
        "|---|---|---|" + "---|" * len(modes),
    ]
    for item_id, per_mode in by_item.items():
        item = next(iter(per_mode.values())).item
        scores = " | ".join(f"{per_mode[m].score:.0%}" if m in per_mode else "-" for m in modes)
        lines.append(f"| {item_id} | {fmt_ts(item.t)} | {item.question} | {scores} |")

    lines += ["", "## Details", ""]
    for item_id, per_mode in by_item.items():
        item = next(iter(per_mode.values())).item
        lines += [f"### {item_id} @ {fmt_ts(item.t)}: {item.question}", ""]
        for i, point in enumerate(item.key_points):
            marks = ", ".join(f"{m} {'✓' if per_mode[m].covered[i] else '✗'}" for m in modes if m in per_mode)
            lines.append(f"- {point} ({marks})")
        for mode in modes:
            if mode in per_mode:
                lines += ["", f"<details><summary>{mode} answer</summary>", "", per_mode[mode].answer, "", "</details>"]
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_evaluation.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the CLI, example dataset and methodology**

`eval/run_eval.py`:

```python
"""Compare transcript-only vs transcript+frames answers on a hand-written question set.

Usage (videos must already be imported through the app, status "ready"):
  cd backend && uv run python ../eval/run_eval.py ../eval/questions.json
"""

import argparse
import datetime
import sys
from pathlib import Path

from unstuck.config import load_settings
from unstuck.db import connect, repo
from unstuck.evaluation import load_items, render_report, run_eval, summarize
from unstuck.llm.factory import build_llm

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    settings = load_settings()
    conn = connect(settings.db_path, settings.embed_dim)
    items = load_items(args.questions)

    not_ready = sorted({
        item.video_id for item in items
        if (video := repo.get_video(conn, item.video_id)) is None or video.status != "ready"
    })
    if not_ready:
        print(f"Import these videos first (status must be 'ready'): {', '.join(not_ready)}", file=sys.stderr)
        return 1

    results = run_eval(conn, build_llm(settings), settings, items)
    out = args.out or RESULTS_DIR / f"report-{datetime.date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(results, settings.chat_model), encoding="utf-8")
    for mode, score in summarize(results).items():
        print(f"{mode}: {score:.0%}")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`eval/questions.example.json`:

```json
[
  {
    "id": "la-01",
    "video_id": "<11-char YouTube id>",
    "t": "03:35",
    "question": "这个矩阵乘出来为什么是这个结果？",
    "key_points": [
      "points out the specific matrix shown on screen",
      "explains row-times-column multiplication",
      "gives the resulting entries correctly"
    ]
  }
]
```

`eval/README.md`:

```markdown
# Answer-quality eval

Question: does seeing the screen make the tutor's answers better?

## Method

1. Pick 3 videos whose meaning depends on what is on screen (a math lecture with a whiteboard, a coding tutorial, a slide-based talk). Import each through the app and wait until its status is "ready".
2. Write about 20 items in `questions.json` (format: `questions.example.json`). Each item is a timestamp, a question a learner would plausibly ask there, and 2–4 key points a good answer must contain. Write key points from watching the video, not from the model's output. Include questions that need the screen ("what does this term mean?") and ones that don't.
3. Run `cd backend && uv run python ../eval/run_eval.py ../eval/questions.json`.
4. Every item is answered twice with the same prompt pipeline:
   - `transcript_only`: no frame descriptions and no live frame
   - `full`: keyframe descriptions, OCR text and the live frame at the pause point
5. An LLM judge marks which key points each answer covers. Score = covered / total. The report lists the mean per mode, per-question scores and both answers.

## Caveats

- The judge is the same free model as the tutor, so absolute numbers are noisy. Compare the two modes against each other, not against other systems.
- About 20 items is a small sample: treat a gap of a few points as noise and look at the per-question table for clear wins and losses.
```

- [ ] **Step 6: Write the real dataset (manual)**

Choose the 3 videos, import them in the app, watch them, and write `eval/questions.json` with ~20 items following `eval/README.md`. This step needs a human watching the videos; do not let a model generate the key points.

- [ ] **Step 7: Run the eval and commit the report**

Run: `cd backend && uv run python ../eval/run_eval.py ../eval/questions.json`
Expected: prints a mean score for `transcript_only` and for `full`, and writes `eval/results/report-<date>.md`. Read the report and check that a few judge verdicts match your own reading of the answers.

```bash
git add backend/unstuck/evaluation.py backend/tests/test_evaluation.py eval/
git commit -m "feat(eval): transcript-only vs visual answer-quality evaluation with report"
```

---

### Task 22: Project README and final verification

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: commands and file names from Parts 1–4.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the README**

`README.md`:

````markdown
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
         │                                   └─ scene detection, min 5 s / max 45 s, phash dedupe, ≤ 1 frame / 10 s
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

FastAPI · SQLite + sqlite-vec · yt-dlp · ffmpeg · PySceneDetect · faster-whisper · imagehash · React + TypeScript + Vite · KaTeX. Models go through one interface (`backend/unstuck/llm`); the defaults are Zhipu's free `glm-4.7-flash` (text) and `glm-4.6v-flash` (vision) plus local `BAAI/bge-m3` embeddings. Any OpenAI-compatible endpoint works by changing `.env`.

## Run it locally

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 20+, ffmpeg (`brew install uv node ffmpeg`), and a Zhipu API key from open.bigmodel.cn.

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

The first import downloads the bge-m3 model (~2 GB) and, for videos without captions, a Whisper model.

## Tests

```bash
cd backend && uv run pytest                                  # unit tests, no network
cd backend && UNSTUCK_IT_URL="<short CC video>" uv run pytest -m slow -s   # real end-to-end
cd frontend && npm test
```

## Evaluation

`eval/` compares answers from the transcript alone against transcript + screen on hand-written questions. See `eval/README.md` and the latest report in `eval/results/`.

## Limits

- yt-dlp downloads are for personal use and demos. YouTube often blocks server IPs, so Unstuck runs locally only.
- Free vision models can misread complex formulas; switch `VISION_MODEL` to a stronger model if needed.
- Videos longer than 2 hours are rejected.
````

- [ ] **Step 2: Final verification**

Run each and confirm the expected result:

```bash
cd backend && uv run pytest
```

Expected: all unit tests PASS.

```bash
cd frontend && npm test && npm run build
```

Expected: tests PASS, build succeeds.

Then follow the README's "Run it locally" section on a clean shell. The app starts, an import completes, and a question gets a grounded, streamed answer.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: project README with architecture, setup, tests and eval"
```

---

**Part 4 done when:** the slow integration test passes, `eval/results/report-*.md` exists with real numbers, and the README instructions work from a clean shell. Phase 1 is then complete.
