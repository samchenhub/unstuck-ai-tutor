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


class Clock:
    """A fake clock that waiting advances, so the worker's waits can be checked without sleeping."""

    def __init__(self):
        self.now, self.waits = 1000.0, []

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return True


def make(settings, llm, clock, **kw):
    return VisionWorker(settings, lambda: llm, wait=clock.wait, clock=clock, **kw)


def test_describes_pending_frames_in_order_then_reindexes(conn, settings, monkeypatch):
    seed(conn)
    refreshes = []
    real_refresh = indexer.refresh_index
    monkeypatch.setattr(indexer, "refresh_index", lambda *a, **kw: refreshes.append(1) or real_refresh(*a, **kw))
    llm = FakeLLM(vision_responses=[REPLY % "slide one", REPLY % "slide two", REPLY % "slide three"])
    clock = Clock()
    make(settings, llm, clock, reindex_every=2).process(VID)

    frames = repo.get_keyframes(conn, VID)
    assert [f.summary for f in frames] == ["slide one", "slide two", "slide three"]
    assert len(refreshes) == 2  # after 2 frames and at the end
    assert clock.waits == [4.0, 4.0, 4.0]  # paced between calls
    prompts = [args[0] for name, args in llm.calls if name == "vision"]
    assert "slide one" in prompts[1]  # each frame gets the previous one's summary
    assert indexer.search(conn, llm, VID, "slide two", k=10)  # the index includes frame descriptions


def test_rate_limits_are_waited_out_not_failed(conn, settings):
    seed(conn, n_frames=2)
    llm = FakeLLM(vision_responses=[REPLY % "a", ModelBusyError("429"), ModelBusyError("429"), REPLY % "b"])
    clock = Clock()
    make(settings, llm, clock, backoff=(60.0, 120.0)).process(VID)
    assert [f.status for f in repo.get_keyframes(conn, VID)] == ["ok", "ok"]
    assert 60.0 in clock.waits and 120.0 in clock.waits


def test_other_errors_retry_a_frame_a_few_times_before_giving_up_on_it(conn, settings):
    seed(conn, n_frames=2)
    llm = FakeLLM(vision_responses=[OSError("reset"), REPLY % "a", OSError("x"), OSError("x"), OSError("x")])
    make(settings, llm, Clock()).process(VID)
    assert [f.status for f in repo.get_keyframes(conn, VID)] == ["ok", "failed"]


def test_a_question_gets_the_quota_first(conn, settings):
    seed(conn, n_frames=1)
    clock = Clock()
    worker = make(settings, FakeLLM(vision_responses=[REPLY % "a"]), clock)
    worker.yield_to_user(30.0)
    worker.process(VID)
    assert sum(clock.waits) >= 30.0 and clock.waits[0] == 30.0


def test_a_question_during_the_pause_extends_it(conn, settings):
    seed(conn, n_frames=1)
    clock = Clock()
    worker = make(settings, FakeLLM(vision_responses=[REPLY % "a"]), clock)
    asked = []

    def wait(seconds):
        if not asked:
            asked.append(1)
            worker.yield_to_user(30.0)  # the learner asks while the worker is pausing
        return clock.wait(seconds)

    worker._wait = wait
    worker.process(VID)
    assert sum(clock.waits) >= 30.0


def test_stopping_ends_the_job(conn, settings):
    seed(conn, n_frames=3)
    worker = VisionWorker(settings, lambda: FakeLLM(vision_responses=[REPLY % "a"] * 3), wait=lambda s: False)
    worker.process(VID)  # wait() returning False means the worker is stopping
    assert all(f.status == "pending" for f in repo.get_keyframes(conn, VID))


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


def test_a_crash_requeues_the_video_a_few_times(conn, settings, monkeypatch):
    seed(conn, n_frames=1)
    worker = make(settings, FakeLLM(vision_responses=[REPLY % "a"]), Clock())
    calls = []

    def boom(conn, video_id):
        calls.append(video_id)
        raise RuntimeError("disk full")

    monkeypatch.setattr(worker, "_process", boom)
    for _ in range(5):
        worker._run_one(VID)
    assert len(calls) == 4  # the first run and three requeues
