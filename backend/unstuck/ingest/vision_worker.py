"""Describes keyframes in the background, at the pace the free vision model allows.

Vision used to be a pipeline stage that hit the free tier's rate limit within a few frames and failed. Here one
thread works through pending frames one at a time with a pause between calls, waits out rate limits (1, 2, 5,
then 10 minutes) instead of failing, steps aside when the learner asks a question, and republishes the index
every few frames so answers can use what is on screen elsewhere in the video.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from unstuck.config import Settings
from unstuck.db import connect, repo
from unstuck.ingest import indexer, vision
from unstuck.llm.base import LLM, ModelBusyError

log = logging.getLogger(__name__)

PACE_S = 4.0
BUSY_BACKOFF_S = (60.0, 120.0, 300.0, 600.0)
REINDEX_EVERY = 10


class VisionWorker:
    def __init__(
        self,
        settings: Settings,
        get_llm: Callable[[], LLM],
        *,
        wait: Callable[[float], bool] | None = None,  # sleeps; False means the worker is stopping
        clock: Callable[[], float] = time.time,
        pace_s: float = PACE_S,
        backoff: tuple[float, ...] = BUSY_BACKOFF_S,
        reindex_every: int = REINDEX_EVERY,
    ):
        self._settings = settings
        self._get_llm = get_llm
        self._clock = clock
        self._pace_s = pace_s
        self._backoff = backoff
        self._reindex_every = reindex_every
        self._cond = threading.Condition()
        self._queue: deque[str] = deque()
        self._stopping = False
        self._quiet_until = 0.0
        self._wait = wait or self._interruptible_wait
        self._thread: threading.Thread | None = None

    # -- control -------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="vision", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()

    def enqueue(self, video_id: str) -> None:
        with self._cond:
            if video_id not in self._queue:
                self._queue.append(video_id)
                self._cond.notify_all()

    def yield_to_user(self, seconds: float = 30.0) -> None:
        """A question needs the vision model now: hold background calls back for a while."""
        with self._cond:
            self._quiet_until = max(self._quiet_until, self._clock() + seconds)

    def _interruptible_wait(self, seconds: float) -> bool:
        with self._cond:
            self._cond.wait_for(lambda: self._stopping, timeout=seconds)
            return not self._stopping

    def _loop(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._stopping or self._queue)
                if self._stopping:
                    return
                video_id = self._queue.popleft()
            try:
                self.process(video_id)
            except Exception:
                log.exception("background vision failed for %s", video_id)

    # -- work ----------------------------------------------------------------
    def process(self, video_id: str) -> None:
        """Describe every pending keyframe of one video (returns early if the worker is stopping)."""
        conn = connect(self._settings.db_path, self._settings.embed_dim)
        try:
            self._process(conn, video_id)
        finally:
            conn.close()

    def _process(self, conn, video_id: str) -> None:
        llm = self._get_llm()
        embeddings: dict[str, list[float]] = {}
        since_reindex = busy = 0
        self._set_state(conn, video_id, "running")
        while True:
            frames = repo.get_keyframes(conn, video_id)
            pending = [f for f in frames if f.status == "pending"]
            if not pending:
                break
            frame = pending[0]
            previous = [f for f in frames if f.status == "ok" and f.t < frame.t][-1:]
            if not self._wait(self._pace_s + max(0.0, self._quiet_until - self._clock())):
                return
            try:
                vision.describe_pending(
                    llm, [*previous, frame], lambda frame_id, desc: repo.save_frame_description(conn, frame_id, desc),
                    sleep=self._wait,
                )
            except ModelBusyError:
                delay = self._backoff[min(busy, len(self._backoff) - 1)]
                busy += 1
                log.info("vision rate-limited for %s; trying again in %.0f s", video_id, delay)
                if not self._wait(delay):
                    return
                continue
            busy = 0
            since_reindex += 1
            if since_reindex >= self._reindex_every:
                indexer.refresh_index(conn, llm, video_id, embeddings)
                since_reindex = 0
        if since_reindex:
            indexer.refresh_index(conn, llm, video_id, embeddings)
        self._set_state(conn, video_id, "done")

    def _set_state(self, conn, video_id: str, state: str) -> None:
        video = repo.get_video(conn, video_id)
        if video is not None and video.stage_states.get("vision") != state:
            repo.update_video(conn, video_id, stage_states={**video.stage_states, "vision": state})
