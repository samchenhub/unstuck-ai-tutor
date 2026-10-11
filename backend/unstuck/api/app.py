import json
import logging
import shutil
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from unstuck import sources
from unstuck.api.runner import PipelineRunner
from unstuck.sources.bilibili import login as bili_login
from unstuck.sources.bilibili.source import client_for as bilibili_client_for
from unstuck.config import Settings, load_settings
from unstuck.db import connect, repo
from unstuck.errors import IngestError
from unstuck.llm.base import LLM
from unstuck.models import Video
from unstuck.tutor.answer import stream_answer

log = logging.getLogger(__name__)


class ImportRequest(BaseModel):
    url: str


class AskRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    t: float = Field(ge=0)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def create_app(settings: Settings | None = None, llm: LLM | None = None, runner=None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or load_settings()
    built: dict[str, LLM] = {}
    build_lock = threading.Lock()

    def get_llm() -> LLM:
        if llm is not None:
            return llm
        if "llm" not in built:
            # The runner, warm-up and request threads can all get here at once; building
            # twice would load the embedding model twice and split the concurrency limit.
            with build_lock:
                if "llm" not in built:
                    from unstuck.llm.factory import build_llm

                    built["llm"] = build_llm(settings)
        return built["llm"]

    runner = runner or PipelineRunner(settings, get_llm)

    def warm_up_embeddings() -> None:
        # Loading the embedding model lazily made the first question wait ~30 s.
        try:
            get_llm().embed(["warmup"])
        except Exception:
            log.warning("embedding warm-up failed", exc_info=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if shutil.which("ffmpeg") is None:
            log.warning("ffmpeg 未安装：无法抽取关键帧和实时截帧")
        conn = connect(settings.db_path, settings.embed_dim)
        try:
            for video in repo.list_videos(conn):
                if video.status == "importing":
                    runner.submit(video.id)
            runner.schedule_pending(conn)
        finally:
            conn.close()
        threading.Thread(target=warm_up_embeddings, name="embed-warmup", daemon=True).start()
        yield
        runner.shutdown()

    app = FastAPI(title="Unstuck", lifespan=lifespan)
    app.state.get_llm = get_llm

    def get_conn():
        conn = connect(settings.db_path, settings.embed_dim)
        try:
            yield conn
        finally:
            conn.close()

    def require_video(conn, video_id: str) -> Video:
        video = repo.get_video(conn, video_id)
        if video is None:
            raise _error(404, "NOT_FOUND", "视频不存在")
        return video

    def video_json(conn, video) -> dict:
        """A video plus how far the background describer has got with its keyframes.

        The 画面理解 state is derived from the frames rather than stored, so it is right for queued videos,
        after a crash, and for databases from when vision was a pipeline stage.
        """
        described, total = repo.count_described_keyframes(conn, video.id)
        states = {**video.stage_states, "vision": _vision_state(video, described, total)}
        return {**asdict(video), "stage_states": states, "frames_described": described, "frames_total": total}

    def start_import(conn, video_id: str) -> dict:
        # A run the user asked for starts a fresh round of automatic retries if it is rate-limited again.
        repo.update_video(conn, video_id, status="importing", auto_retries=0)
        runner.submit(video_id)
        return video_json(conn, repo.get_video(conn, video_id))

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/videos")
    def list_videos(conn=Depends(get_conn)):
        return [video_json(conn, v) for v in repo.list_videos(conn)]

    @app.post("/api/videos", status_code=202)
    def import_video(req: ImportRequest, conn=Depends(get_conn)):
        try:
            ref = sources.resolve(req.url, settings)
        except IngestError as exc:
            raise _error(400, exc.code, exc.message) from exc
        created = repo.insert_video(
            conn, ref.video_id, ref.url, source=ref.source, course_id=ref.course_id, part=ref.part
        )
        video = repo.get_video(conn, ref.video_id)
        if created or video.status == "failed":
            return start_import(conn, ref.video_id)
        return video_json(conn, video)

    @app.get("/api/videos/{video_id}")
    def get_video(video_id: str, conn=Depends(get_conn)):
        return video_json(conn, require_video(conn, video_id))

    @app.post("/api/videos/{video_id}/retry", status_code=202)
    def retry(video_id: str, conn=Depends(get_conn)):
        require_video(conn, video_id)
        repo.reset_failed_keyframes(conn, video_id)  # described again in the background after the run
        return start_import(conn, video_id)

    @app.get("/api/videos/{video_id}/chats")
    def list_chats(video_id: str, conn=Depends(get_conn)):
        require_video(conn, video_id)
        return [asdict(c) for c in repo.list_chats(conn, video_id)]

    def source_call(fn):
        try:
            return fn()
        except IngestError as exc:
            raise _error(502, exc.code, exc.message) from exc

    @app.get("/api/videos/{video_id}/playback")
    def playback(video_id: str, quality: int = 720, conn=Depends(get_conn)):
        video = require_video(conn, video_id)
        return asdict(source_call(lambda: sources.for_video(video, settings).playback(video_id, quality)))

    @app.get("/api/videos/{video_id}/parts")
    def parts(video_id: str, conn=Depends(get_conn)):
        video = require_video(conn, video_id)
        items = source_call(lambda: sources.for_video(video, settings).parts(video_id))
        imported = {v.id for v in repo.list_videos(conn)}
        return [{**asdict(p), "imported": p.video_id in imported} for p in items]

    @app.get("/api/bilibili/status")
    def bilibili_status():
        return sources.bilibili_status(settings)

    # Only QR codes this app issued may be polled: polling an attacker's own key from another site would log
    # this app into their account. Logging out cancels logins in progress.
    issued_logins: set[str] = set()
    logins_lock = threading.Lock()

    @app.post("/api/bilibili/login")
    def bilibili_login_start():
        started = source_call(lambda: bili_login.start())
        with logins_lock:
            issued_logins.add(started.key)
        return {"key": started.key, "qr": started.qr}

    @app.get("/api/bilibili/login/{key}")
    def bilibili_login_poll(key: str):
        with logins_lock:
            if key not in issued_logins:
                raise _error(404, "NOT_FOUND", "这个登录二维码不存在或已失效")
        state = source_call(lambda: bili_login.poll(key, settings.bilibili_cookie_file))
        if state in ("done", "expired", "failed"):
            with logins_lock:
                issued_logins.discard(key)
        if state == "done":
            bilibili_client_for(settings).reset_login()
        return {"state": state}

    @app.delete("/api/bilibili/login", status_code=204)
    def bilibili_logout():
        with logins_lock:
            issued_logins.clear()
        bili_login.logout(settings.bilibili_cookie_file)
        bilibili_client_for(settings).reset_login()

    @app.post("/api/videos/{video_id}/ask")
    def ask(video_id: str, req: AskRequest, conn=Depends(get_conn)):
        video = require_video(conn, video_id)
        if not video.ask_ready:
            raise _error(409, "NOT_READY", "字幕还在处理中，请稍后再提问")
        runner.yield_to_user()  # the answer may look at the screen; give it the vision quota first

        def events() -> Iterator[str]:
            # The streaming body outlives the request dependency, so it uses its own connection.
            stream_conn = connect(settings.db_path, settings.embed_dim)
            try:
                for event in stream_answer(stream_conn, get_llm(), settings, video_id, req.question, req.t):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                stream_conn.close()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _vision_state(video, described: int, total: int) -> str:
    keyframes = video.stage_states.get("keyframes")
    if video.visual_mode == "transcript_only" or keyframes in ("failed", "skipped"):
        return "skipped"
    if keyframes != "done":
        return "pending"
    if total == 0:
        return "skipped"
    return "running" if described < total else "done"
