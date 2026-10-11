import pytest

from unstuck.db import repo
from unstuck.errors import IngestError
from unstuck.ingest.pipeline import SKIPPED, STAGES, Pipeline, StageContext
from unstuck.llm import ModelBusyError

VID = "abcdefghijk"


@pytest.fixture
def ctx(conn, fake_llm, settings):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    return StageContext(conn=conn, llm=fake_llm, settings=settings, video_id=VID)


def recording(order, overrides=None):
    overrides = overrides or {}

    def make(name):
        def stage(ctx):
            order.append(name)
            if name in overrides:
                return overrides[name](ctx)
            return None

        return stage

    return {name: make(name) for name in STAGES}


def raise_(exc):
    def fn(ctx):
        raise exc

    return fn


def test_fresh_run_executes_all_stages(ctx):
    order = []
    Pipeline(recording(order)).run(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert order == list(STAGES)
    assert video.status == "ready"
    assert set(video.stage_states.values()) == {"done"}


def test_resumes_after_completed_stages(ctx):
    repo.update_video(ctx.conn, VID, stage_states={"metadata": "done", "transcript": "done", "download": "done"})
    order = []
    Pipeline(recording(order)).run(ctx)
    assert order == ["keyframes", "vision", "timeline", "index", "outline"]


def test_rerunning_a_stage_resets_downstream_stages(ctx):
    states = {name: "done" for name in STAGES}
    states["vision"] = "failed"
    repo.update_video(ctx.conn, VID, stage_states=states)
    order = []
    Pipeline(recording(order)).run(ctx)
    assert order == ["vision", "timeline", "index", "outline"]


def test_running_state_left_by_a_crash_is_resumed(ctx):
    repo.update_video(ctx.conn, VID, stage_states={"metadata": "done", "transcript": "running"})
    order = []
    Pipeline(recording(order)).run(ctx)
    assert order[0] == "transcript"


def test_critical_failure_stops_the_run(ctx):
    order = []
    stages = recording(order, {"transcript": raise_(IngestError("NO_TRANSCRIPT", "没有字幕"))})
    Pipeline(stages).run(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert order == ["metadata", "transcript"]
    assert video.status == "failed"
    assert video.error_code == "NO_TRANSCRIPT"
    assert video.error_message == "没有字幕"
    assert video.stage_states["transcript"] == "failed"
    assert video.stage_states["download"] == "pending"


def test_non_critical_failure_continues(ctx):
    order = []
    Pipeline(recording(order, {"vision": raise_(RuntimeError("model down"))})).run(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert order == list(STAGES)
    assert video.status == "ready"
    assert video.stage_states["vision"] == "failed"
    assert video.stage_states["outline"] == "done"
    assert video.error_code == "STAGE_FAILED"
    assert "model down" in video.error_message


def test_model_busy_in_non_critical_stage_is_reported_in_chinese(ctx):
    order = []
    Pipeline(recording(order, {"outline": raise_(ModelBusyError("x"))})).run(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert video.status == "ready"
    assert video.stage_states["outline"] == "failed"
    assert video.error_code == "MODEL_BUSY"
    assert video.error_message.startswith("outline 阶段")
    assert "稍后" in video.error_message


def test_skipped_stages_are_recorded_and_not_rerun(ctx):
    order = []
    Pipeline(recording(order, {"keyframes": lambda c: SKIPPED})).run(ctx)
    assert repo.get_video(ctx.conn, VID).stage_states["keyframes"] == "skipped"

    order.clear()
    Pipeline(recording(order)).run(ctx)
    assert order == []
    assert repo.get_video(ctx.conn, VID).status == "ready"


def test_retry_clears_previous_error(ctx):
    Pipeline(recording([], {"metadata": raise_(IngestError("VIDEO_UNAVAILABLE", "x"))})).run(ctx)
    assert repo.get_video(ctx.conn, VID).status == "failed"
    Pipeline(recording([])).run(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert video.status == "ready"
    assert video.error_code is None


def test_stage_context_creates_video_dir(ctx, settings):
    assert ctx.video_dir == settings.video_dir(VID)
    assert ctx.video_dir.is_dir()


def test_run_reports_whether_a_stage_was_rate_limited(ctx):
    assert Pipeline(recording([], {"vision": raise_(ModelBusyError("x"))})).run(ctx) is True
    repo.update_video(ctx.conn, VID, stage_states={})
    assert Pipeline(recording([], {"outline": raise_(IngestError("OUTLINE_FAILED", "x"))})).run(ctx) is False
    repo.update_video(ctx.conn, VID, stage_states={})
    assert Pipeline(recording([])).run(ctx) is False
