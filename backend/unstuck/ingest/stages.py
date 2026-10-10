import logging

from unstuck import sources
from unstuck.db import repo
from unstuck.errors import IngestError
from unstuck.ingest import files, indexer, keyframes, outline, timeline, transcribe, vision
from unstuck.ingest.pipeline import SKIPPED, StageContext, StageFn

log = logging.getLogger(__name__)

INFO_FILE = "info.json"


def _rebuild_timeline(ctx: StageContext) -> None:
    video = repo.get_video(ctx.conn, ctx.video_id)
    segments = timeline.build_segments(
        repo.get_cues(ctx.conn, ctx.video_id), repo.get_keyframes(ctx.conn, ctx.video_id), video.duration
    )
    repo.replace_segments(ctx.conn, ctx.video_id, segments)


def _transcript_only(ctx: StageContext) -> bool:
    return repo.get_video(ctx.conn, ctx.video_id).visual_mode == "transcript_only"


def _source(ctx: StageContext):
    return sources.for_video(repo.get_video(ctx.conn, ctx.video_id), ctx.settings)


def stage_metadata(ctx: StageContext) -> None:
    src = _source(ctx)
    info = src.fetch_info(ctx.video_id)
    meta = src.check_info(info, ctx.settings.max_duration_s)
    files.save_info(info, ctx.video_dir / INFO_FILE)
    repo.update_video(
        ctx.conn, ctx.video_id,
        title=meta.title, channel=meta.channel, duration=meta.duration,
        part_title=meta.part_title, parts_total=meta.parts_total, thumbnail=meta.thumbnail,
    )


def stage_transcript(ctx: StageContext) -> None:
    src = _source(ctx)
    info = files.load_info(ctx.video_dir / INFO_FILE)
    cues = src.fetch_captions(info)
    if not cues:
        log.info("no captions for %s, transcribing audio", ctx.video_id)
        audio = src.download_audio(ctx.video_id, ctx.video_dir)
        cues = transcribe.transcribe(audio, ctx.settings.whisper_model)
    if not cues:
        raise IngestError("NO_TRANSCRIPT", "没有字幕，语音转写也没有得到内容")
    repo.replace_cues(ctx.conn, ctx.video_id, cues)
    # Transcript-only timeline + index so the learner can start asking right away;
    # the timeline/index stages rebuild it with visual information later.
    _rebuild_timeline(ctx)
    indexer.build_index(ctx.conn, ctx.llm, ctx.video_id)
    repo.update_video(ctx.conn, ctx.video_id, ask_ready=True)


def stage_download(ctx: StageContext) -> None:
    try:
        _source(ctx).download_video(ctx.video_id, ctx.video_dir)
    except Exception:
        log.warning("download failed for %s, continuing transcript-only", ctx.video_id, exc_info=True)
        repo.update_video(ctx.conn, ctx.video_id, visual_mode="transcript_only")
        return
    repo.update_video(ctx.conn, ctx.video_id, visual_mode="full")


def stage_keyframes(ctx: StageContext) -> str | None:
    if _transcript_only(ctx):
        return SKIPPED
    video_path = files.find_video_file(ctx.video_dir)
    if video_path is None:
        raise IngestError("DOWNLOAD_FAILED", "找不到已下载的视频文件")
    duration = repo.get_video(ctx.conn, ctx.video_id).duration
    frames = keyframes.extract_keyframes(video_path, duration, ctx.video_dir / "frames")
    repo.replace_keyframes(ctx.conn, ctx.video_id, frames)
    return None


def stage_vision(ctx: StageContext) -> str | None:
    if _transcript_only(ctx):
        return SKIPPED
    failed = vision.describe_pending(
        ctx.llm,
        repo.get_keyframes(ctx.conn, ctx.video_id),
        lambda frame_id, desc: repo.save_frame_description(ctx.conn, frame_id, desc),
    )
    if failed:
        raise IngestError("VISION_PARTIAL", f"{failed} 张关键帧的画面理解失败，可以重试")
    return None


def stage_timeline(ctx: StageContext) -> None:
    _rebuild_timeline(ctx)


def stage_index(ctx: StageContext) -> None:
    indexer.build_index(ctx.conn, ctx.llm, ctx.video_id)


def stage_outline(ctx: StageContext) -> None:
    video = repo.get_video(ctx.conn, ctx.video_id)
    chapters = outline.generate_outline(
        ctx.llm, repo.get_segments(ctx.conn, ctx.video_id), video.title, video.duration
    )
    if not chapters:
        raise IngestError("OUTLINE_FAILED", "大纲生成失败，可以重试")
    repo.update_video(ctx.conn, ctx.video_id, outline=chapters)


DEFAULT_STAGES: dict[str, StageFn] = {
    "metadata": stage_metadata,
    "transcript": stage_transcript,
    "download": stage_download,
    "keyframes": stage_keyframes,
    "vision": stage_vision,
    "timeline": stage_timeline,
    "index": stage_index,
    "outline": stage_outline,
}
