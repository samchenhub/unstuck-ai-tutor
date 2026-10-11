import json
import time

import pytest
from fastapi.testclient import TestClient

from unstuck import sources
from unstuck.api.app import create_app
from unstuck.db import repo
from unstuck.errors import IngestError
from unstuck.sources.base import Part, Playback


class FakeRunner:
    def __init__(self):
        self.submitted = []

    def submit(self, video_id):
        self.submitted.append(video_id)

    def schedule_pending(self, conn):
        self.scheduled = True

    def yield_to_user(self, seconds=30.0):
        self.yielded = True

    def shutdown(self):
        pass


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def client(settings, fake_llm, runner):
    with TestClient(create_app(settings=settings, llm=fake_llm, runner=runner)) as c:
        yield c


def sse_events(text):
    return [json.loads(block[len("data: "):]) for block in text.split("\n\n") if block.startswith("data: ")]


def test_startup_warms_up_the_embedding_model(client, fake_llm):
    deadline = time.monotonic() + 2
    while ("embed", ["warmup"]) not in fake_llm.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ("embed", ["warmup"]) in fake_llm.calls


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_import_invalid_url(client, runner):
    r = client.post("/api/videos", json={"url": "https://vimeo.com/1"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_URL"
    assert runner.submitted == []


def test_import_creates_video_once(client, runner):
    r = client.post("/api/videos", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.status_code == 202
    assert r.json()["id"] == "dQw4w9WgXcQ"
    assert r.json()["status"] == "importing"
    client.post("/api/videos", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert runner.submitted == ["dQw4w9WgXcQ"]


def test_reimporting_a_failed_video_restarts_it(client, runner, conn):
    repo.insert_video(conn, "dQw4w9WgXcQ", "u")
    repo.update_video(conn, "dQw4w9WgXcQ", status="failed")
    r = client.post("/api/videos", json={"url": "dQw4w9WgXcQ"})
    assert r.json()["status"] == "importing"
    assert runner.submitted == ["dQw4w9WgXcQ"]


def test_list_and_get(client, conn):
    repo.insert_video(conn, "aaaaaaaaaaa", "u")
    repo.insert_video(conn, "bbbbbbbbbbb", "u")
    assert [v["id"] for v in client.get("/api/videos").json()] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]
    assert client.get("/api/videos/aaaaaaaaaaa").json()["stage_states"] == {"vision": "pending"}
    missing = client.get("/api/videos/zzzzzzzzzzz")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"


def test_retry(client, runner, conn):
    repo.insert_video(conn, "aaaaaaaaaaa", "u")
    repo.update_video(conn, "aaaaaaaaaaa", status="ready")
    r = client.post("/api/videos/aaaaaaaaaaa/retry")
    assert r.status_code == 202
    assert r.json()["status"] == "importing"
    assert runner.submitted == ["aaaaaaaaaaa"]


def test_ask_requires_ready_video(client, conn):
    repo.insert_video(conn, "aaaaaaaaaaa", "u")
    r = client.post("/api/videos/aaaaaaaaaaa/ask", json={"question": "hi", "t": 1})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NOT_READY"


@pytest.mark.parametrize("body", [{"question": "   ", "t": 1}, {"question": "ok", "t": -1}])
def test_ask_validates_input(client, seeded_video, body):
    assert client.post(f"/api/videos/{seeded_video}/ask", json=body).status_code == 422


def test_ask_streams_answer_and_saves_chats(client, seeded_video, fake_llm, runner):
    fake_llm.chat_responses.append("Because [01:40] shows the formula.")

    r = client.post(f"/api/videos/{seeded_video}/ask", json={"question": "Why?", "t": 110})

    assert r.status_code == 200
    assert runner.yielded  # background vision steps aside for the question
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Because [01:40] shows the formula."
    assert events[-1]["type"] == "done"
    chats = client.get(f"/api/videos/{seeded_video}/chats").json()
    assert [c["role"] for c in chats] == ["user", "assistant"]
    assert chats[0]["t_at_ask"] == 110


def test_startup_resumes_importing_videos(settings, fake_llm, conn):
    repo.insert_video(conn, "aaaaaaaaaaa", "u")
    repo.insert_video(conn, "bbbbbbbbbbb", "u")
    repo.update_video(conn, "bbbbbbbbbbb", status="ready")
    runner = FakeRunner()
    with TestClient(create_app(settings=settings, llm=fake_llm, runner=runner)):
        pass
    assert runner.submitted == ["aaaaaaaaaaa"]
    assert runner.scheduled  # automatic retries planned before the restart are planned again


def test_get_llm_builds_the_real_llm_exactly_once_across_threads(settings, runner, monkeypatch):
    import threading

    built = []

    def slow_build(_settings):
        time.sleep(0.05)
        obj = object()
        built.append(obj)
        return obj

    monkeypatch.setattr("unstuck.llm.factory.build_llm", slow_build)
    app = create_app(settings=settings, runner=runner)
    results = []
    threads = [threading.Thread(target=lambda: results.append(app.state.get_llm())) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1
    assert len(results) == 6 and all(r is built[0] for r in results)


def test_startup_warns_when_ffmpeg_is_missing(settings, fake_llm, runner, monkeypatch, caplog):
    monkeypatch.setattr("unstuck.api.app.shutil.which", lambda name: None)
    with caplog.at_level("WARNING"):
        with TestClient(create_app(settings=settings, llm=fake_llm, runner=runner)):
            pass
    assert "ffmpeg 未安装" in caplog.text


class FakeBiliSource:
    name = "bilibili"

    def playback(self, video_id, quality=720):
        return Playback(kind="mp4", url="https://upos.bilivideo.com/v.mp4", expires_at=123.0, duration=50.0)

    def parts(self, video_id):
        return [Part(1, "概念", 100.0, "BV1Ex4y1G7nG_p1"), Part(2, "法则", 50.0, "BV1Ex4y1G7nG_p2")]


def test_import_bilibili_link(client, runner):
    r = client.post("/api/videos", json={"url": "https://www.bilibili.com/video/BV1Ex4y1G7nG?p=2"})
    assert r.status_code == 202
    body = r.json()
    assert (body["id"], body["source"], body["course_id"], body["part"]) == ("BV1Ex4y1G7nG_p2", "bilibili", "BV1Ex4y1G7nG", 2)
    assert runner.submitted == ["BV1Ex4y1G7nG_p2"]


def test_import_rejects_paid_course(client):
    r = client.post("/api/videos", json={"url": "https://www.bilibili.com/cheese/play/ss1"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "UNSUPPORTED_URL"


def test_playback_and_parts(client, conn, monkeypatch):
    repo.insert_video(conn, "BV1Ex4y1G7nG_p2", "u", source="bilibili", course_id="BV1Ex4y1G7nG", part=2)
    monkeypatch.setattr(sources, "for_video", lambda video, settings: FakeBiliSource())
    pb = client.get("/api/videos/BV1Ex4y1G7nG_p2/playback").json()
    assert pb == {"kind": "mp4", "url": "https://upos.bilivideo.com/v.mp4", "expires_at": 123.0, "duration": 50.0,
                  "backup_urls": []}
    parts = client.get("/api/videos/BV1Ex4y1G7nG_p2/parts").json()
    assert [(p["part"], p["imported"]) for p in parts] == [(1, False), (2, True)]


def test_playback_source_error_is_502(client, conn, monkeypatch):
    repo.insert_video(conn, "BV1Ex4y1G7nG_p1", "u", source="bilibili", course_id="BV1Ex4y1G7nG", part=1)

    class Broken(FakeBiliSource):
        def playback(self, video_id, quality=720):
            raise IngestError("BILIBILI_BLOCKED", "B站暂时限制了访问，请稍后重试")

    monkeypatch.setattr(sources, "for_video", lambda video, settings: Broken())
    r = client.get("/api/videos/BV1Ex4y1G7nG_p1/playback")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "BILIBILI_BLOCKED"


def test_bilibili_status(client, monkeypatch):
    monkeypatch.setattr(sources, "bilibili_status", lambda settings: {"logged_in": False, "cookies_browser": "chrome"})
    assert client.get("/api/bilibili/status").json() == {"logged_in": False, "cookies_browser": "chrome"}


def test_manual_retry_restarts_the_automatic_retry_count(client, conn, runner):
    repo.insert_video(conn, "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    repo.update_video(conn, "dQw4w9WgXcQ", status="ready", auto_retries=5)
    assert client.post("/api/videos/dQw4w9WgXcQ/retry").status_code == 202
    assert repo.get_video(conn, "dQw4w9WgXcQ").auto_retries == 0


def test_videos_report_how_many_frames_are_described(client, conn):
    from unstuck.models import FrameDescription, NewKeyframe

    repo.insert_video(conn, "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    first, _second = repo.replace_keyframes(conn, "dQw4w9WgXcQ", [
        NewKeyframe(1.0, "/f/1.jpg", "0" * 16), NewKeyframe(50.0, "/f/2.jpg", "f" * 16)])
    repo.save_frame_description(conn, first.id, FrameDescription("slide", "s", "", ""))
    video = client.get("/api/videos/dQw4w9WgXcQ").json()
    assert (video["frames_described"], video["frames_total"]) == (1, 2)
    assert client.get("/api/videos").json()[0]["frames_total"] == 2


def test_the_vision_state_follows_the_frames(client, conn):
    from unstuck.models import FrameDescription, NewKeyframe

    vid = "dQw4w9WgXcQ"
    repo.insert_video(conn, vid, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    repo.update_video(conn, vid, status="ready", stage_states={"keyframes": "running", "vision": "failed"})
    assert client.get(f"/api/videos/{vid}").json()["stage_states"]["vision"] == "pending"
    repo.update_video(conn, vid, stage_states={"keyframes": "done", "vision": "failed"})  # an old database
    first, _ = repo.replace_keyframes(conn, vid, [NewKeyframe(1.0, "/f/1.jpg", "0" * 16), NewKeyframe(9.0, "/f/2.jpg", "f" * 16)])
    assert client.get(f"/api/videos/{vid}").json()["stage_states"]["vision"] == "running"
    repo.save_frame_description(conn, first.id, FrameDescription("slide", "s", "", ""))
    repo.save_frame_description(conn, _.id, None)
    video = client.get(f"/api/videos/{vid}").json()
    assert video["stage_states"]["vision"] == "done"
    repo.add_described_keyframe(conn, vid, NewKeyframe(5.0, "/live/5.jpg", "1" * 16),
                                FrameDescription("live", "what was on screen", "", ""))
    video = client.get(f"/api/videos/{vid}").json()
    assert (video["frames_described"], video["frames_total"]) == (2, 2)  # live frames are not part of the job
    repo.update_video(conn, vid, stage_states={"keyframes": "skipped"})
    assert client.get(f"/api/videos/{vid}").json()["stage_states"]["vision"] == "skipped"


def test_retry_gives_failed_frames_another_chance(client, conn, runner):
    from unstuck.models import NewKeyframe

    vid = "dQw4w9WgXcQ"
    repo.insert_video(conn, vid, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    repo.update_video(conn, vid, status="ready")
    (frame,) = repo.replace_keyframes(conn, vid, [NewKeyframe(1.0, "/f/1.jpg", "0" * 16)])
    repo.save_frame_description(conn, frame.id, None)
    client.post(f"/api/videos/{vid}/retry")
    assert [f.status for f in repo.get_keyframes(conn, vid)] == ["pending"]
