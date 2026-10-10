from pathlib import Path

import pytest

from unstuck.db import repo
from unstuck.errors import IngestError
from unstuck.ingest import stages, vision, youtube
from unstuck.ingest.pipeline import SKIPPED, StageContext
from unstuck.llm import FakeLLM
from unstuck.models import Cue, NewKeyframe, Segment

VID = "abcdefghijk"


@pytest.fixture
def ctx(conn, fake_llm, settings):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    repo.update_video(conn, VID, duration=60.0, title="Vectors 101")
    return StageContext(conn=conn, llm=fake_llm, settings=settings, video_id=VID)


def sentences(n):
    return [Cue(i * 5.0, (i + 1) * 5.0, f"sentence number {i}.") for i in range(n)]


def test_metadata_stores_info(ctx, monkeypatch):
    info = {"id": VID, "title": "Dot products", "channel": "3B", "duration": 300}
    monkeypatch.setattr(youtube, "fetch_info", lambda video_id: info)
    stages.stage_metadata(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert (video.title, video.channel, video.duration) == ("Dot products", "3B", 300.0)
    assert youtube.load_info(ctx.video_dir / "info.json") == info


def test_metadata_rejects_too_long(ctx, monkeypatch):
    monkeypatch.setattr(youtube, "fetch_info", lambda video_id: {"id": VID, "duration": 99999})
    with pytest.raises(IngestError) as err:
        stages.stage_metadata(ctx)
    assert err.value.code == "TOO_LONG"


def test_transcript_from_captions_enables_asking(ctx, monkeypatch):
    youtube.save_info({"id": VID}, ctx.video_dir / "info.json")
    cues = sentences(12)
    monkeypatch.setattr(youtube, "fetch_captions", lambda info: cues)
    monkeypatch.setattr(youtube, "download_audio", lambda *a: pytest.fail("must not download audio"))

    stages.stage_transcript(ctx)

    assert repo.get_cues(ctx.conn, VID) == cues
    assert repo.get_video(ctx.conn, VID).ask_ready is True
    segments = repo.get_segments(ctx.conn, VID)
    assert segments
    assert repo.search_vectors(ctx.conn, VID, ctx.llm.embed(["sentence"])[0], k=1)


def test_transcript_falls_back_to_whisper(ctx, monkeypatch, tmp_path):
    youtube.save_info({"id": VID}, ctx.video_dir / "info.json")
    monkeypatch.setattr(youtube, "fetch_captions", lambda info: None)
    monkeypatch.setattr(youtube, "download_audio", lambda video_id, out_dir: tmp_path / "audio.m4a")
    monkeypatch.setattr(stages.transcribe, "transcribe", lambda path, model: sentences(3))

    stages.stage_transcript(ctx)

    assert len(repo.get_cues(ctx.conn, VID)) == 3


def test_transcript_with_nothing_fails(ctx, monkeypatch, tmp_path):
    youtube.save_info({"id": VID}, ctx.video_dir / "info.json")
    monkeypatch.setattr(youtube, "fetch_captions", lambda info: None)
    monkeypatch.setattr(youtube, "download_audio", lambda video_id, out_dir: tmp_path / "audio.m4a")
    monkeypatch.setattr(stages.transcribe, "transcribe", lambda path, model: [])
    with pytest.raises(IngestError) as err:
        stages.stage_transcript(ctx)
    assert err.value.code == "NO_TRANSCRIPT"


def test_download_failure_degrades_to_transcript_only(ctx, monkeypatch):
    def fail(video_id, out_dir):
        raise IngestError("DOWNLOAD_FAILED", "blocked")

    monkeypatch.setattr(youtube, "download_video", fail)
    stages.stage_download(ctx)
    assert repo.get_video(ctx.conn, VID).visual_mode == "transcript_only"
    assert stages.stage_keyframes(ctx) == SKIPPED
    assert stages.stage_vision(ctx) == SKIPPED


def test_keyframes_stage_stores_frames(ctx, monkeypatch):
    (ctx.video_dir / "video.mp4").write_bytes(b"fake")
    frames = [NewKeyframe(1.0, "/f/1.jpg", "0" * 16)]
    monkeypatch.setattr(stages.keyframes, "extract_keyframes", lambda path, duration, out_dir: frames)
    stages.stage_keyframes(ctx)
    assert [f.t for f in repo.get_keyframes(ctx.conn, VID)] == [1.0]


def test_vision_partial_failure_raises_after_saving_the_rest(ctx, monkeypatch):
    monkeypatch.setattr(vision.time, "sleep", lambda s: None)
    repo.replace_keyframes(ctx.conn, VID, [NewKeyframe(1.0, "/f/1.jpg", "a"), NewKeyframe(20.0, "/f/2.jpg", "b")])
    ctx.llm = FakeLLM(vision_responses=[
        RuntimeError("429"), RuntimeError("429"),
        '{"kind": "slide", "summary": "ok", "ocr_text": "", "change_note": ""}',
    ])
    with pytest.raises(IngestError) as err:
        stages.stage_vision(ctx)
    assert err.value.code == "VISION_PARTIAL"
    assert [f.status for f in repo.get_keyframes(ctx.conn, VID)] == ["failed", "ok"]


def test_timeline_and_index_use_keyframes(ctx):
    repo.replace_cues(ctx.conn, VID, sentences(12))
    (frame,) = repo.replace_keyframes(ctx.conn, VID, [NewKeyframe(10.0, "/f/1.jpg", "a")])
    stages.stage_timeline(ctx)
    assert frame.id in repo.get_segments(ctx.conn, VID)[0].keyframe_ids
    stages.stage_index(ctx)
    assert repo.search_vectors(ctx.conn, VID, ctx.llm.embed(["sentence"])[0], k=1)


def test_outline_stage_stores_outline(ctx):
    repo.replace_segments(ctx.conn, VID, [Segment(0, 30, "intro"), Segment(30, 60, "dot products")])
    ctx.llm = FakeLLM(chat_responses=['{"chapters": [{"start": 0, "title": "Intro", "summary": "s"}]}'])
    stages.stage_outline(ctx)
    assert repo.get_video(ctx.conn, VID).outline == [{"start": 0.0, "title": "Intro", "summary": "s"}]


def test_default_stages_cover_all():
    from unstuck.ingest.pipeline import STAGES

    assert set(stages.DEFAULT_STAGES) == set(STAGES)


def test_unexpected_download_error_also_degrades_to_transcript_only(ctx, monkeypatch):
    def fail(video_id, out_dir):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(youtube, "download_video", fail)
    stages.stage_download(ctx)
    assert repo.get_video(ctx.conn, VID).visual_mode == "transcript_only"
