import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from unstuck.config import Settings
from unstuck.db import connect, repo
from unstuck.ingest.pipeline import Pipeline, StageContext
from unstuck.llm.base import LLM

log = logging.getLogger(__name__)

# The free models are rate-limited for minutes at a time: retry a rate-limited import after these delays.
AUTO_RETRY_DELAYS_S = (120, 300, 600, 1200, 1800)
GAVE_UP_MESSAGE = "部分步骤被免费模型限流，已自动重试 5 次仍未成功，请稍后点击重试"


class PipelineRunner:
    """Runs imports in a small in-process thread pool (single user, no task queue)."""

    def __init__(
        self, settings: Settings, get_llm: Callable[[], LLM], pipeline: Pipeline | None = None, max_workers: int = 2,
        *, timer=threading.Timer, clock: Callable[[], float] = time.time,
    ):
        self._settings = settings
        self._get_llm = get_llm
        self._pipeline = pipeline or Pipeline()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ingest")
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self._timer = timer
        self._clock = clock
        self._timers: dict[str, object] = {}

    def submit(self, video_id: str) -> Future | None:
        with self._lock:
            if video_id in self._running:
                return None
            self._running.add(video_id)
        return self._executor.submit(self._run, video_id)

    def _run(self, video_id: str) -> None:
        conn = None
        try:
            conn = connect(self._settings.db_path, self._settings.embed_dim)
            ctx = StageContext(conn=conn, llm=self._get_llm(), settings=self._settings, video_id=video_id)
            rate_limited = self._pipeline.run(ctx)
            self._plan_retry(conn, video_id, bool(rate_limited))
        except Exception as exc:
            log.exception("pipeline crashed for %s", video_id)
            self._mark_failed(conn, video_id, exc)
        finally:
            if conn is not None:
                conn.close()
            with self._lock:
                self._running.discard(video_id)

    def _plan_retry(self, conn: sqlite3.Connection, video_id: str, rate_limited: bool) -> None:
        """After a rate-limited run, come back later on our own (the free tier recovers in minutes)."""
        video = repo.get_video(conn, video_id)
        if not rate_limited:
            if video.auto_retries or video.auto_retry_at:
                repo.update_video(conn, video_id, auto_retries=0, auto_retry_at=0.0)
            return
        if video.auto_retries >= len(AUTO_RETRY_DELAYS_S):
            repo.update_video(conn, video_id, auto_retry_at=0.0, error_message=GAVE_UP_MESSAGE)
            return
        delay = AUTO_RETRY_DELAYS_S[video.auto_retries]
        repo.update_video(conn, video_id, auto_retries=video.auto_retries + 1, auto_retry_at=self._clock() + delay)
        log.info("%s was rate-limited; retrying automatically in %d s", video_id, delay)
        self._schedule(video_id, delay)

    def _schedule(self, video_id: str, delay: float) -> None:
        handle = self._timer(delay, self.submit, args=(video_id,))
        handle.daemon = True
        with self._lock:
            previous = self._timers.pop(video_id, None)
            self._timers[video_id] = handle
        if previous is not None:
            previous.cancel()
        handle.start()

    def schedule_pending(self, conn: sqlite3.Connection) -> None:
        """Re-plan automatic retries that were pending when the app stopped."""
        now = self._clock()
        for video in repo.list_videos(conn):
            if video.auto_retry_at > 0 and video.status != "importing":
                self._schedule(video.id, max(0.0, video.auto_retry_at - now))

    def _mark_failed(self, conn, video_id: str, exc: Exception) -> None:
        if conn is None:  # the database itself was unreachable; nothing can be recorded
            return
        try:
            repo.update_video(conn, video_id, status="failed", error_code="INTERNAL", error_message=f"内部错误：{exc}"[:300])
        except Exception:
            log.exception("could not record the failure for %s", video_id)

    def shutdown(self) -> None:
        with self._lock:
            timers, self._timers = list(self._timers.values()), {}
        for handle in timers:
            handle.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
