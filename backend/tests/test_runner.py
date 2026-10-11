import threading

from unstuck.api.runner import PipelineRunner
from unstuck.db import repo

VID = "abcdefghijk"


class RecordingPipeline:
    def __init__(self):
        self.ran = []

    def run(self, ctx):
        self.ran.append(ctx.video_id)


def test_runs_pipeline_in_background(settings, fake_llm):
    pipeline = RecordingPipeline()
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=pipeline)
    runner.submit(VID).result(timeout=5)
    assert pipeline.ran == [VID]
    runner.shutdown()


def test_ignores_duplicate_submissions_while_running(settings, fake_llm):
    gate = threading.Event()

    class Blocking:
        def run(self, ctx):
            gate.wait(5)

    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=Blocking())
    first = runner.submit(VID)
    assert runner.submit(VID) is None
    gate.set()
    first.result(timeout=5)
    assert runner.submit(VID) is not None
    runner.shutdown()


def test_crash_marks_video_failed(settings, fake_llm, conn):
    repo.insert_video(conn, VID, "u")

    class Crashing:
        def run(self, ctx):
            raise RuntimeError("boom")

    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=Crashing())
    runner.submit(VID).result(timeout=5)
    video = repo.get_video(conn, VID)
    assert video.status == "failed"
    assert video.error_code == "INTERNAL"
    runner.shutdown()


def test_connect_failure_still_releases_the_video(settings, fake_llm, monkeypatch):
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=RecordingPipeline())

    def boom(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("unstuck.api.runner.connect", boom)
    runner.submit(VID).result(timeout=5)
    assert runner.submit(VID) is not None
    runner.shutdown()


class FakeTimers:
    def __init__(self):
        self.started = []

    def __call__(self, delay, fn, args):
        timer = self
        entry = {"delay": delay, "fn": fn, "args": args, "cancelled": False}

        class Handle:
            daemon = False

            def start(self):
                timer.started.append(entry)

            def cancel(self):
                entry["cancelled"] = True

        return Handle()


class RateLimited:
    def __init__(self, results):
        self.results = list(results)

    def run(self, ctx):
        return self.results.pop(0)


def test_rate_limited_run_schedules_an_automatic_retry(settings, fake_llm, conn):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    timers = FakeTimers()
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=RateLimited([True, True, False]),
                            timer=timers, clock=lambda: 1000.0)
    runner.submit(VID).result(timeout=5)
    video = repo.get_video(conn, VID)
    assert (video.auto_retries, video.auto_retry_at) == (1, 1000.0 + 120)
    assert [t["delay"] for t in timers.started] == [120]

    timers.started[-1]["fn"](*timers.started[-1]["args"]).result(timeout=5)  # the timer fires: submit again
    assert repo.get_video(conn, VID).auto_retries == 2
    assert [t["delay"] for t in timers.started] == [120, 300]

    timers.started[-1]["fn"](*timers.started[-1]["args"]).result(timeout=5)  # not rate-limited this time
    video = repo.get_video(conn, VID)
    assert (video.auto_retries, video.auto_retry_at) == (0, 0.0)
    assert len(timers.started) == 2
    runner.shutdown()


def test_automatic_retries_stop_after_the_last_delay(settings, fake_llm, conn):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    repo.update_video(conn, VID, status="ready", auto_retries=5, error_code="MODEL_BUSY", error_message="x")
    timers = FakeTimers()
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=RateLimited([True]), timer=timers, clock=lambda: 0.0)
    runner.submit(VID).result(timeout=5)
    video = repo.get_video(conn, VID)
    assert timers.started == [] and video.auto_retry_at == 0.0
    assert "点击重试" in video.error_message


def test_scheduled_retries_survive_a_restart(settings, fake_llm, conn):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    repo.update_video(conn, VID, status="ready", auto_retries=1, auto_retry_at=1100.0)
    timers = FakeTimers()
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=RateLimited([]), timer=timers, clock=lambda: 1000.0)
    runner.schedule_pending(conn)
    assert [(t["delay"], t["args"]) for t in timers.started] == [(100.0, (VID,))]
    runner.shutdown()
    assert timers.started[0]["cancelled"]


def test_a_successful_run_cancels_the_pending_retry(settings, fake_llm, conn):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    timers = FakeTimers()
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=RateLimited([True, False]), timer=timers, clock=lambda: 0.0)
    runner.submit(VID).result(timeout=5)
    runner.submit(VID).result(timeout=5)  # e.g. the user clicked 重试 and it got through
    assert timers.started[0]["cancelled"]
    runner.shutdown()


def test_nothing_is_scheduled_or_submitted_after_shutdown(settings, fake_llm, conn):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    timers = FakeTimers()
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=RateLimited([True]), timer=timers, clock=lambda: 0.0)
    runner.shutdown()
    assert runner.submit(VID) is None
    runner._schedule(VID, 10)
    assert timers.started == []


class RecordingVision:
    def __init__(self):
        self.enqueued, self.yielded, self.stopped = [], [], False

    def start(self):
        pass

    def enqueue(self, video_id):
        self.enqueued.append(video_id)

    def yield_to_user(self, seconds=30.0):
        self.yielded.append(seconds)

    def stop(self):
        self.stopped = True


def test_frames_left_after_an_import_go_to_the_background_vision_job(settings, fake_llm, conn):
    from unstuck.models import NewKeyframe

    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    repo.replace_keyframes(conn, VID, [NewKeyframe(1.0, "/f/1.jpg", "0" * 16)])
    vision = RecordingVision()
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=RateLimited([False]), vision=vision)
    runner.submit(VID).result(timeout=5)
    assert vision.enqueued == [VID]
    runner.yield_to_user()
    assert vision.yielded == [30.0]
    runner.shutdown()
    assert vision.stopped


def test_startup_resumes_background_vision(settings, fake_llm, conn):
    from unstuck.models import NewKeyframe

    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    repo.update_video(conn, VID, status="ready")
    repo.replace_keyframes(conn, VID, [NewKeyframe(1.0, "/f/1.jpg", "0" * 16)])
    repo.insert_video(conn, "bbbbbbbbbbb", "u")
    repo.update_video(conn, "bbbbbbbbbbb", status="ready")
    vision = RecordingVision()
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=RateLimited([]), vision=vision)
    runner.schedule_pending(conn)
    assert vision.enqueued == [VID]
    runner.shutdown()


def test_startup_clears_errors_left_by_the_old_vision_stage(settings, fake_llm, conn):
    from unstuck.ingest.pipeline import STAGES

    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    repo.update_video(conn, VID, status="ready", stage_states={**{n: "done" for n in STAGES}, "vision": "failed"},
                      error_code="MODEL_BUSY", error_message="已自动重试 5 次仍未成功", auto_retries=5)
    from unstuck.models import NewKeyframe

    repo.replace_keyframes(conn, VID, [NewKeyframe(1.0, "/f/1.jpg", "0" * 16)])
    vision = RecordingVision()
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=RateLimited([]), vision=vision)
    runner.schedule_pending(conn)
    video = repo.get_video(conn, VID)
    assert (video.error_code, video.error_message, video.auto_retries) == (None, None, 0)
    assert vision.enqueued == [VID]  # its frames are still described in the background
    runner.shutdown()


def test_startup_keeps_errors_of_unfinished_stages(settings, fake_llm, conn):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    repo.update_video(conn, VID, status="ready", stage_states={"outline": "failed"}, error_code="OUTLINE_FAILED",
                      error_message="大纲生成失败")
    runner = PipelineRunner(settings, lambda: fake_llm, pipeline=RateLimited([]), vision=RecordingVision())
    runner.schedule_pending(conn)
    assert repo.get_video(conn, VID).error_code == "OUTLINE_FAILED"
    runner.shutdown()
