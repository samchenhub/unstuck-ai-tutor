from unstuck.db import repo
from unstuck.ingest import indexer
from unstuck.ingest.vision_worker import VisionWorker
from unstuck.llm import FakeLLM, ModelBusyError
from unstuck.models import Cue, NewKeyframe

VID = "abcdefghijk"
REPLY = '{"kind": "slide", "summary": "%s", "ocr_text": "", "change_note": ""}'


def seed(conn, n_frames=3):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    repo.update_video(conn, VID, duration=300.0, status="ready", stage_states={"metadata": "done", "keyframes": "done"})
    repo.replace_cues(conn, VID, [Cue(i * 10.0, i * 10.0 + 9, f"sentence {i}.") for i in range(30)])
    repo.replace_keyframes(conn, VID, [NewKeyframe(30.0 + i * 45, f"/f/{i}.jpg", f"{i:016x}") for i in range(n_frames)])


def make(settings, llm, waits, clock=lambda: 1000.0, **kw):
    return VisionWorker(settings, lambda: llm, wait=lambda s: waits.append(s) or True, clock=clock, **kw)


def test_describes_pending_frames_in_order_then_reindexes(conn, settings, monkeypatch):
    seed(conn)
    refreshes = []
    real_refresh = indexer.refresh_index
    monkeypatch.setattr(indexer, "refresh_index", lambda *a, **kw: refreshes.append(1) or real_refresh(*a, **kw))
    llm = FakeLLM(vision_responses=[REPLY % "slide one", REPLY % "slide two", REPLY % "slide three"])
    waits = []
    make(settings, llm, waits, reindex_every=2).process(VID)

    frames = repo.get_keyframes(conn, VID)
    assert [f.summary for f in frames] == ["slide one", "slide two", "slide three"]
    assert len(refreshes) == 2  # after 2 frames and at the end
    assert waits == [4.0, 4.0, 4.0]  # paced between calls
    video = repo.get_video(conn, VID)
    assert video.stage_states["vision"] == "done" and video.stage_states["metadata"] == "done"
    hits = indexer.search(conn, llm, VID, "slide two", k=10)
    assert hits  # the index was rebuilt with frame descriptions


def test_rate_limits_are_waited_out_not_failed(conn, settings):
    seed(conn, n_frames=2)
    llm = FakeLLM(vision_responses=[REPLY % "a", ModelBusyError("429"), ModelBusyError("429"), REPLY % "b"])
    waits = []
    make(settings, llm, waits, backoff=(60.0, 120.0)).process(VID)
    assert [f.status for f in repo.get_keyframes(conn, VID)] == ["ok", "ok"]
    assert 60.0 in waits and 120.0 in waits


def test_a_question_gets_the_quota_first(conn, settings):
    seed(conn, n_frames=1)
    now = [1000.0]
    waits = []
    worker = make(settings, FakeLLM(vision_responses=[REPLY % "a"]), waits, clock=lambda: now[0])
    worker.yield_to_user(30.0)
    worker.process(VID)
    assert waits[0] == 34.0  # the usual pause plus the time held back for the question


def test_stopping_ends_the_job(conn, settings):
    seed(conn, n_frames=3)
    worker = VisionWorker(settings, lambda: FakeLLM(vision_responses=[REPLY % "a"] * 3), wait=lambda s: False)
    worker.process(VID)  # wait() returning False means the worker is stopping
    assert all(f.status == "pending" for f in repo.get_keyframes(conn, VID))


def test_a_video_without_pending_frames_is_marked_done(conn, settings):
    seed(conn, n_frames=0)
    make(settings, FakeLLM(), []).process(VID)
    assert repo.get_video(conn, VID).stage_states["vision"] == "done"


def test_queue_runs_in_a_background_thread(conn, settings):
    import time

    seed(conn, n_frames=1)
    worker = VisionWorker(settings, lambda: FakeLLM(vision_responses=[REPLY % "a"]), pace_s=0.0)
    worker.start()
    worker.enqueue(VID)
    deadline = time.time() + 5
    while time.time() < deadline and repo.get_keyframes(conn, VID)[0].status != "ok":
        time.sleep(0.02)
    worker.stop()
    assert repo.get_keyframes(conn, VID)[0].status == "ok"
