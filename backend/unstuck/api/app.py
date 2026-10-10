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

    def start_import(conn, video_id: str) -> dict:
        repo.update_video(conn, video_id, status="importing")
        runner.submit(video_id)
        return asdict(repo.get_video(conn, video_id))

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/videos")
    def list_videos(conn=Depends(get_conn)):
        return [asdict(v) for v in repo.list_videos(conn)]

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
        return asdict(video)

    @app.get("/api/videos/{video_id}")
    def get_video(video_id: str, conn=Depends(get_conn)):
        return asdict(require_video(conn, video_id))

    @app.post("/api/videos/{video_id}/retry", status_code=202)
    def retry(video_id: str, conn=Depends(get_conn)):
        require_video(conn, video_id)
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

    @app.post("/api/videos/{video_id}/ask")
    def ask(video_id: str, req: AskRequest, conn=Depends(get_conn)):
        video = require_video(conn, video_id)
        if not video.ask_ready:
            raise _error(409, "NOT_READY", "字幕还在处理中，请稍后再提问")

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
