import shutil

from unstuck import media, sources
from unstuck.db import repo
from unstuck.llm import FakeLLM
from unstuck.sources.base import Playback
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


def test_bilibili_without_local_file_uses_stream_url(conn, settings, sample_video, monkeypatch):
    repo.insert_video(conn, "BV1Ex4y1G7nG_p1", "u", source="bilibili", course_id="BV1Ex4y1G7nG", part=1)

    class StreamSource:
        frames_from_stream = True
        stream_headers = {}

        def playback(self, video_id, quality=720):
            return Playback(kind="mp4", url=str(sample_video))  # ffmpeg reads a URL or a path alike

    monkeypatch.setattr(sources, "for_video", lambda video, s: StreamSource())
    llm = FakeLLM(vision_responses=["白板上写着导数定义"])
    assert live_frame_description(conn, llm, settings, "BV1Ex4y1G7nG_p1", 1.0, "这是什么") == "白板上写着导数定义"


def test_youtube_without_local_file_still_returns_none(conn, settings):
    repo.insert_video(conn, VID, "u")
    assert live_frame_description(conn, FakeLLM(), settings, VID, 1.0, "q") is None


def _record_extract(monkeypatch):
    calls = []

    def fake(video, t, out_path, **kwargs):
        calls.append((video, kwargs))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"jpg")
        return out_path

    monkeypatch.setattr("unstuck.tutor.frame.media.extract_frame", fake)
    return calls


def test_stream_capture_sends_browser_headers(conn, settings, monkeypatch):
    from unstuck.sources.bilibili.client import HEADERS

    repo.insert_video(conn, "BV1Ex4y1G7nG_p1", "u", source="bilibili", course_id="BV1Ex4y1G7nG", part=1)

    class StreamSource:
        frames_from_stream = True
        stream_headers = HEADERS

        def playback(self, video_id, quality=720):
            return Playback(kind="mp4", url="https://upos.bilivideo.com/v.mp4")

    monkeypatch.setattr(sources, "for_video", lambda video, s: StreamSource())
    calls = _record_extract(monkeypatch)
    live_frame_description(conn, FakeLLM(vision_responses=["x"]), settings, "BV1Ex4y1G7nG_p1", 1.0, "q")
    assert calls == [("https://upos.bilivideo.com/v.mp4", {"headers": HEADERS})]


def test_local_file_capture_sends_no_headers(conn, settings, sample_video, monkeypatch):
    install_video(settings, sample_video)
    calls = _record_extract(monkeypatch)
    live_frame_description(conn, FakeLLM(vision_responses=["x"]), settings, VID, 1.0, "q")
    assert len(calls) == 1 and calls[0][1] == {"headers": None}


def test_a_described_live_frame_is_kept_as_a_keyframe(conn, settings, sample_video):
    install_video(settings, sample_video)
    live_frame_description(conn, FakeLLM(vision_responses=["白板上写着链式法则"]), settings, VID, 1.4, "q")
    frames = repo.get_keyframes(conn, VID)
    assert [(f.t, f.status, f.summary) for f in frames] == [(1.0, "ok", "白板上写着链式法则")]
