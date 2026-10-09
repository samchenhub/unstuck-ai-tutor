import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from unstuck.config import Settings
from unstuck.db import connect, repo
from unstuck.ingest.pipeline import Pipeline, StageContext
from unstuck.llm.base import LLM

log = logging.getLogger(__name__)


class PipelineRunner:
    """Runs imports in a small in-process thread pool (single user, no task queue)."""

    def __init__(
        self, settings: Settings, get_llm: Callable[[], LLM], pipeline: Pipeline | None = None, max_workers: int = 2
    ):
        self._settings = settings
        self._get_llm = get_llm
        self._pipeline = pipeline or Pipeline()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ingest")
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, video_id: str) -> Future | None:
        with self._lock:
            if video_id in self._running:
                return None
            self._running.add(video_id)
        return self._executor.submit(self._run, video_id)

    def _run(self, video_id: str) -> None:
        conn = connect(self._settings.db_path, self._settings.embed_dim)
        try:
            ctx = StageContext(conn=conn, llm=self._get_llm(), settings=self._settings, video_id=video_id)
            self._pipeline.run(ctx)
        except Exception as exc:
            log.exception("pipeline crashed for %s", video_id)
            repo.update_video(conn, video_id, status="failed", error_code="INTERNAL", error_message=f"内部错误：{exc}"[:300])
        finally:
            conn.close()
            with self._lock:
                self._running.discard(video_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
