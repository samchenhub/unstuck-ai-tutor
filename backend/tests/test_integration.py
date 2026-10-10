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
