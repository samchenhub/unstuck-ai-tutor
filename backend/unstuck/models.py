from dataclasses import dataclass, field


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Scene:
    start: float
    end: float


@dataclass(frozen=True)
class NewKeyframe:
    t: float
    image_path: str
    phash: str


@dataclass(frozen=True)
class FrameDescription:
    kind: str
    summary: str
    ocr_text: str
    change_note: str


@dataclass(frozen=True)
class Keyframe:
    id: int
    video_id: str
    t: float
    image_path: str
    phash: str
    summary: str
    ocr_text: str
    kind: str
    change_note: str
    status: str


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    transcript: str
    keyframe_ids: list[int] = field(default_factory=list)
    id: int | None = None


@dataclass(frozen=True)
class Chat:
    id: int
    video_id: str
    role: str
    content: str
    t_at_ask: float | None
    complete: bool
    created_at: str


@dataclass
class Video:
    id: str
    url: str
    title: str
    channel: str
    duration: float
    status: str
    ask_ready: bool
    stage_states: dict[str, str]
    visual_mode: str
    outline: list[dict] | None
    error_code: str | None
    error_message: str | None
    created_at: str
    source: str = "youtube"
    course_id: str | None = None
    part: int | None = None
    part_title: str = ""
    parts_total: int = 1
    thumbnail: str = ""
    transcribed_until: float = 0.0  # seconds covered by local speech-to-text so far (0 for caption transcripts)
