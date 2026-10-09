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
