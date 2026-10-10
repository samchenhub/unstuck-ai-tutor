"""Real Bilibili import + answer with the user's Chrome login.

Run: cd backend && UNSTUCK_IT_BILI_URL="https://www.bilibili.com/video/BV…?p=1" uv run pytest -m slow -s tests/test_integration_bilibili.py
"""

import os
from dataclasses import replace

import pytest

from unstuck import sources
from unstuck.config import load_settings
from unstuck.db import connect, repo
from unstuck.ingest.pipeline import Pipeline, StageContext
from unstuck.llm.factory import build_llm
from unstuck.tutor.answer import stream_answer

URL = os.getenv("UNSTUCK_IT_BILI_URL")

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not URL, reason="set UNSTUCK_IT_BILI_URL")]


def test_import_play_and_answer_bilibili(tmp_path):
    settings = replace(load_settings(), data_dir=tmp_path)
    print("bilibili login:", sources.bilibili_status(settings))
    ref = sources.resolve(URL, settings)
    llm = build_llm(settings)
    conn = connect(settings.db_path, settings.embed_dim)
    try:
        repo.insert_video(conn, ref.video_id, ref.url, source=ref.source, course_id=ref.course_id, part=ref.part)
        Pipeline().run(StageContext(conn=conn, llm=llm, settings=settings, video_id=ref.video_id))
        video = repo.get_video(conn, ref.video_id)
        print("stages:", video.stage_states, "error:", video.error_code, video.error_message)
        assert video.status == "ready" and video.source == "bilibili" and video.duration > 0
        assert repo.get_cues(conn, ref.video_id)

        playback = sources.for_video(video, settings).playback(ref.video_id)
        assert playback.kind == "mp4" and playback.url.startswith("https://")

        events = list(stream_answer(conn, llm, settings, ref.video_id, "这一段在讲什么？", min(60.0, video.duration / 2)))
        print("answer:", "".join(e.get("text", "") for e in events))
        assert events[-1]["type"] == "done", events[-1]
    finally:
        conn.close()
