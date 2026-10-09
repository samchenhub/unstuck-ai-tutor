import pytest

from unstuck.errors import IngestError
from unstuck.ingest.youtube import check_video_info, find_video_file, parse_video_id, pick_caption_track


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
        "  dQw4w9WgXcQ ",
    ],
)
def test_parse_video_id(url):
    assert parse_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", ["", "https://vimeo.com/123", "https://www.youtube.com/watch?v=short"])
def test_parse_video_id_invalid(url):
    with pytest.raises(IngestError) as err:
        parse_video_id(url)
    assert err.value.code == "INVALID_URL"


def test_check_video_info_ok():
    meta = check_video_info(
        {"id": "dQw4w9WgXcQ", "title": "T", "channel": "C", "duration": 212, "live_status": "not_live"},
        max_duration_s=7200,
    )
    assert (meta.id, meta.title, meta.channel, meta.duration) == ("dQw4w9WgXcQ", "T", "C", 212.0)


def test_check_video_info_uses_uploader_when_no_channel():
    meta = check_video_info({"id": "x", "title": "T", "uploader": "U", "duration": 10}, 7200)
    assert meta.channel == "U"


@pytest.mark.parametrize(
    "info,code",
    [
        ({"id": "x", "duration": 100, "live_status": "is_live"}, "LIVE_NOT_SUPPORTED"),
        ({"id": "x", "duration": 0, "live_status": "is_upcoming"}, "LIVE_NOT_SUPPORTED"),
        ({"id": "x", "duration": 7201}, "TOO_LONG"),
        ({"id": "x"}, "VIDEO_UNAVAILABLE"),
    ],
)
def test_check_video_info_rejects(info, code):
    with pytest.raises(IngestError) as err:
        check_video_info(info, max_duration_s=7200)
    assert err.value.code == code


def fmt(ext, url):
    return {"ext": ext, "url": url}


def test_prefers_manual_track_in_video_language():
    info = {
        "language": "en",
        "subtitles": {"en": [fmt("vtt", "m-vtt"), fmt("json3", "m-json3")]},
        "automatic_captions": {"en": [fmt("json3", "a-json3")]},
    }
    assert pick_caption_track(info) == ("m-json3", "manual")


def test_falls_back_to_original_language_auto_track():
    info = {
        "language": "en",
        "subtitles": {"live_chat": [fmt("json", "chat")]},
        "automatic_captions": {
            "fr": [fmt("json3", "a-fr")],
            "en": [fmt("json3", "a-en")],
            "en-orig": [fmt("json3", "a-en-orig")],
        },
    }
    assert pick_caption_track(info) == ("a-en-orig", "auto")


def test_chinese_auto_track():
    info = {"language": "zh-Hans", "subtitles": {}, "automatic_captions": {"zh-Hans": [fmt("json3", "a-zh")]}}
    assert pick_caption_track(info) == ("a-zh", "auto")


def test_manual_track_in_other_language_beats_nothing():
    info = {"subtitles": {"de": [fmt("json3", "m-de")]}, "automatic_captions": {}}
    assert pick_caption_track(info) == ("m-de", "manual")


def test_no_tracks():
    assert pick_caption_track({"subtitles": {}, "automatic_captions": {}}) is None


def test_find_video_file(tmp_path):
    assert find_video_file(tmp_path) is None
    (tmp_path / "video.mp4.part").write_bytes(b"x")
    assert find_video_file(tmp_path) is None
    (tmp_path / "video.mp4").write_bytes(b"x")
    assert find_video_file(tmp_path) == tmp_path / "video.mp4"
