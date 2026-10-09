from unstuck.models import Keyframe


def make_keyframe(
    id: int, t: float, *, status: str = "ok", summary: str = "", ocr_text: str = "",
    kind: str = "other", video_id: str = "abcdefghijk",
) -> Keyframe:
    return Keyframe(
        id=id, video_id=video_id, t=t, image_path=f"/frames/{id}.jpg", phash="0" * 16,
        summary=summary, ocr_text=ocr_text, kind=kind, change_note="", status=status,
    )
