import shutil

from unstuck.db import repo
from unstuck.llm import FakeLLM
from unstuck.tutor.frame import live_frame_description

VID = "abcdefghijk"


def install_video(settings, sample_video):
    target = settings.video_dir(VID)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(sample_video, target / "video.mp4")


def test_cache_hit_skips_capture(conn, settings):
    repo.put_frame_cache(conn, VID, 12, "cached view")
    llm = FakeLLM()
    assert live_frame_description(conn, llm, settings, VID, 12.7, "why?") == "cached view"
    assert llm.calls == []


def test_no_downloaded_video_returns_none(conn, settings):
    assert live_frame_description(conn, FakeLLM(), settings, VID, 1.0, "why?") is None


def test_captures_describes_and_caches(conn, settings, sample_video):
    install_video(settings, sample_video)
    llm = FakeLLM(vision_responses=["A colourful test card"])

    first = live_frame_description(conn, llm, settings, VID, 1.4, "what is this pattern?")
    second = live_frame_description(conn, llm, settings, VID, 1.9, "and now?")

    assert first == second == "A colourful test card"
    vision_calls = [args for name, args in llm.calls if name == "vision"]
    assert len(vision_calls) == 1
    assert "what is this pattern?" in vision_calls[0][0]
    assert repo.get_frame_cache(conn, VID, 1) == "A colourful test card"


def test_capture_failure_returns_none_and_caches_nothing(conn, settings, sample_video):
    install_video(settings, sample_video)
    assert live_frame_description(conn, FakeLLM(), settings, VID, 10.0, "q") is None
    assert repo.get_frame_cache(conn, VID, 10) is None


def test_vision_failure_returns_none(conn, settings, sample_video):
    install_video(settings, sample_video)
    llm = FakeLLM(vision_responses=[RuntimeError("model down")])
    assert live_frame_description(conn, llm, settings, VID, 1.0, "q") is None
    assert repo.get_frame_cache(conn, VID, 1) is None
