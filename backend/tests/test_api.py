import json
import time

import pytest
from fastapi.testclient import TestClient

from unstuck.api.app import create_app
from unstuck.db import repo


class FakeRunner:
    def __init__(self):
        self.submitted = []

    def submit(self, video_id):
        self.submitted.append(video_id)

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
    assert client.get("/api/videos/aaaaaaaaaaa").json()["stage_states"] == {}
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


def test_ask_streams_answer_and_saves_chats(client, seeded_video, fake_llm):
    fake_llm.chat_responses.append("Because [01:40] shows the formula.")

    r = client.post(f"/api/videos/{seeded_video}/ask", json={"question": "Why?", "t": 110})

    assert r.status_code == 200
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
