import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from unstuck.config import Settings
from unstuck.db import repo
from unstuck.jsonfix import loads_lenient
from unstuck.llm.base import LLM
from unstuck.timefmt import fmt_ts, parse_ts
from unstuck.tutor.answer import prepare_messages

log = logging.getLogger(__name__)

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
    failed: bool = False
    judge_unparsed: bool = False

    @property
    def score(self) -> float:
        return sum(self.covered) / len(self.covered) if self.covered else 0.0


REQUIRED_FIELDS = ("id", "video_id", "t", "question", "key_points")


def _parse_item(index: int, entry) -> EvalItem:
    if not isinstance(entry, dict):
        raise ValueError(f"item {index}: expected an object")
    for name in REQUIRED_FIELDS:
        if name not in entry:
            raise ValueError(f"item {index}: missing field {name!r}")
    key_points = entry["key_points"]
    if not isinstance(key_points, list) or not key_points:
        raise ValueError(f"item {index}: key_points must be a non-empty list")
    t = entry["t"]
    try:
        seconds = parse_ts(t) if isinstance(t, str) else float(t)
    except ValueError as exc:
        raise ValueError(f"item {index}: {exc}") from exc
    return EvalItem(
        id=str(entry["id"]),
        video_id=entry["video_id"],
        t=seconds,
        question=entry["question"],
        key_points=[str(p) for p in key_points],
    )


def load_items(path: Path) -> list[EvalItem]:
    items = [_parse_item(i, entry) for i, entry in enumerate(json.loads(path.read_text(encoding="utf-8")))]
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ValueError(f"duplicate id: {item.id!r}")
        seen.add(item.id)
    return items


def build_judge_messages(item: EvalItem, answer: str) -> list[dict]:
    points = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(item.key_points))
    return [{"role": "user", "content": JUDGE_PROMPT.format(question=item.question, points=points, answer=answer)}]


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes")
    return value is True


def _covered_list(raw: str):
    obj = loads_lenient(raw)
    covered = obj.get("covered") if isinstance(obj, dict) else obj
    return covered if isinstance(covered, list) else None


def judgement_usable(raw: str) -> bool:
    return _covered_list(raw) is not None


def parse_judgement(raw: str, n_points: int) -> list[bool]:
    covered = _covered_list(raw)
    if covered is None:
        return [False] * n_points
    flags = [_truthy(v) for v in covered[:n_points]]
    return flags + [False] * (n_points - len(flags))


def _check_visual_modes(conn: sqlite3.Connection, items: list[EvalItem], modes) -> None:
    if "full" not in modes:
        return
    for video_id in sorted({item.video_id for item in items}):
        video = repo.get_video(conn, video_id)
        if video is None:
            raise ValueError(f"unknown video {video_id}")
        if video.visual_mode != "full":
            raise ValueError(
                f"video {video_id} has visual_mode {video.visual_mode!r}: the 'full' mode would silently "
                "match transcript_only"
            )


def _evaluate(conn, llm, settings, item: EvalItem, mode: str) -> ItemResult:
    messages = prepare_messages(
        conn, llm, settings, item.video_id, item.question, item.t,
        use_visual=(mode == "full"), history=[],
    )
    answer = llm.chat(messages)
    verdict = llm.chat(build_judge_messages(item, answer), json_mode=True)
    return ItemResult(
        item, mode, answer, parse_judgement(verdict, len(item.key_points)),
        judge_unparsed=not judgement_usable(verdict),
    )


def run_eval(
    conn: sqlite3.Connection, llm: LLM, settings: Settings, items: list[EvalItem], modes=MODES
) -> list[ItemResult]:
    _check_visual_modes(conn, items, modes)
    results = []
    for item in items:
        for mode in modes:
            try:
                results.append(_evaluate(conn, llm, settings, item, mode))
            except Exception as exc:
                log.exception("eval failed for %s (%s)", item.id, mode)
                results.append(ItemResult(
                    item, mode, f"(error: {exc})", [False] * len(item.key_points), failed=True
                ))
    return results


def summarize(results: list[ItemResult]) -> dict[str, float]:
    by_mode: dict[str, list[float]] = {}
    for result in results:
        by_mode.setdefault(result.mode, []).append(result.score)
    return {mode: sum(scores) / len(scores) for mode, scores in by_mode.items()}


def _cell(text: str) -> str:
    """Make text safe for one Markdown table cell or heading line."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _score_cell(result: ItemResult | None) -> str:
    if result is None:
        return "-"
    if result.failed:
        return "⚠ failed"
    return f"{result.score:.0%}" + (" ⚠ judge" if result.judge_unparsed else "")


def render_report(results: list[ItemResult], model_name: str) -> str:
    summary = summarize(results)
    modes = list(summary)
    by_item: dict[str, dict[str, ItemResult]] = {}
    for result in results:
        by_item.setdefault(result.item.id, {})[result.mode] = result
    failed = [r for r in results if r.failed]
    unparsed = [r for r in results if r.judge_unparsed and not r.failed]

    lines = [
        "# Unstuck answer-quality eval",
        "",
        f"Answer and judge model: `{model_name}`. "
        "Score = share of hand-written key points the answer covers, judged by the LLM.",
        "",
        "| Mode | Mean score |",
        "|---|---|",
        *[f"| {mode} | {score:.0%} |" for mode, score in summary.items()],
    ]
    if failed or unparsed:
        lines += [
            "",
            f"⚠ {len(failed)} answer(s) failed and {len(unparsed)} judge reply(ies) were unparseable; "
            "both count as 0% in the means above. See Problems under Details.",
        ]
    lines += [
        "",
        "## Per question",
        "",
        "| ID | Time | Question | " + " | ".join(modes) + " |",
        "|---|---|---|" + "---|" * len(modes),
    ]
    for item_id, per_mode in by_item.items():
        item = next(iter(per_mode.values())).item
        scores = " | ".join(_score_cell(per_mode.get(m)) for m in modes)
        lines.append(f"| {_cell(item_id)} | {fmt_ts(item.t)} | {_cell(item.question)} | {scores} |")

    lines += ["", "## Details", ""]
    if failed or unparsed:
        lines += ["### Problems", ""]
        lines += [f"- ⚠ {_cell(r.item.id)} ({r.mode}): {_cell(r.answer)}" for r in failed]
        lines += [f"- ⚠ {_cell(r.item.id)} ({r.mode}): judge reply could not be parsed" for r in unparsed]
        lines.append("")
    for item_id, per_mode in by_item.items():
        item = next(iter(per_mode.values())).item
        lines += [f"### {_cell(item_id)} @ {fmt_ts(item.t)}: {_cell(item.question)}", ""]
        for i, point in enumerate(item.key_points):
            marks = ", ".join(f"{m} {'✓' if per_mode[m].covered[i] else '✗'}" for m in modes if m in per_mode)
            lines.append(f"- {' '.join(point.split())} ({marks})")
        for mode in modes:
            if mode in per_mode:
                lines += ["", f"<details><summary>{mode} answer</summary>", "", per_mode[mode].answer, "", "</details>"]
        lines.append("")
    return "\n".join(lines)
