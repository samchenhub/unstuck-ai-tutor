import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from unstuck.config import Settings
from unstuck.db import repo
from unstuck.errors import IngestError
from unstuck.llm.base import LLM

log = logging.getLogger(__name__)

STAGES = ("metadata", "transcript", "download", "keyframes", "vision", "timeline", "index", "outline")
CRITICAL = frozenset({"metadata", "transcript"})
SKIPPED = "skipped"
_FINISHED = ("done", SKIPPED)


@dataclass
class StageContext:
    conn: sqlite3.Connection
    llm: LLM
    settings: Settings
    video_id: str

    @property
    def video_dir(self) -> Path:
        path = self.settings.video_dir(self.video_id)
        path.mkdir(parents=True, exist_ok=True)
        return path


StageFn = Callable[[StageContext], str | None]


def _describe(stage: str, exc: Exception) -> tuple[str, str]:
    if isinstance(exc, IngestError):
        return exc.code, exc.message
    return "STAGE_FAILED", f"{stage} 阶段失败：{exc}"[:500]


class Pipeline:
    def __init__(self, stages: Mapping[str, StageFn] | None = None):
        if stages is None:
            from unstuck.ingest.stages import DEFAULT_STAGES

            stages = DEFAULT_STAGES
        missing = set(STAGES) - set(stages)
        if missing:
            raise ValueError(f"missing stage functions: {sorted(missing)}")
        self._stages = dict(stages)

    def run(self, ctx: StageContext) -> None:
        video = repo.get_video(ctx.conn, ctx.video_id)
        if video is None:
            raise ValueError(f"unknown video {ctx.video_id}")
        states = {name: video.stage_states.get(name, "pending") for name in STAGES}
        start = next((i for i, name in enumerate(STAGES) if states[name] not in _FINISHED), None)
        if start is None:
            repo.update_video(ctx.conn, ctx.video_id, status="ready")
            return

        for name in STAGES[start:]:
            states[name] = "pending"
        repo.update_video(
            ctx.conn, ctx.video_id, status="importing", stage_states=states, error_code=None, error_message=None
        )

        last_error: tuple[str, str] | None = None
        for name in STAGES[start:]:
            states[name] = "running"
            repo.update_video(ctx.conn, ctx.video_id, stage_states=states)
            try:
                result = self._stages[name](ctx)
                states[name] = SKIPPED if result == SKIPPED else "done"
            except Exception as exc:
                log.exception("stage %s failed for %s", name, ctx.video_id)
                states[name] = "failed"
                code, message = _describe(name, exc)
                if name in CRITICAL:
                    repo.update_video(
                        ctx.conn, ctx.video_id, status="failed", stage_states=states,
                        error_code=code, error_message=message,
                    )
                    return
                last_error = (code, message)
            repo.update_video(ctx.conn, ctx.video_id, stage_states=states)

        code, message = last_error or (None, None)
        repo.update_video(
            ctx.conn, ctx.video_id, status="ready", stage_states=states, error_code=code, error_message=message
        )
