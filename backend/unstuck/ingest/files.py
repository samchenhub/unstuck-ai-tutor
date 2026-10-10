import json
from pathlib import Path

_VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov"}


def save_info(info: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")


def load_info(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_video_file(video_dir: Path) -> Path | None:
    for path in sorted(video_dir.glob("video.*")):
        if path.suffix in _VIDEO_SUFFIXES and path.is_file():
            return path
    return None
