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
