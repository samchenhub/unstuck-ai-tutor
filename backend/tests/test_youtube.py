import pytest
from yt_dlp.utils import DownloadError

from unstuck.errors import IngestError
from unstuck.ingest import youtube
from unstuck.ingest.youtube import (
    check_video_info,
    download_audio,
    download_video,
    fetch_captions,
    fetch_info,
    find_video_file,
    parse_video_id,
    pick_caption_track,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "youtu.be/dQw4w9WgXcQ",
        "  dQw4w9WgXcQ ",
    ],
)
def test_parse_video_id(url):
    assert parse_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://vimeo.com/123",
        "https://www.youtube.com/watch?v=short",
        "https://vimeo.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQEXTRA",
        "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
    ],
)
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


def test_check_video_info_accepts_exact_max_duration():
    meta = check_video_info({"id": "x", "title": "T", "duration": 7200}, max_duration_s=7200)
    assert meta.duration == 7200.0


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


def test_unknown_language_prefers_orig_track_over_translations():
    info = {
        "subtitles": {},
        "automatic_captions": {
            "en": [fmt("json3", "a-en")],
            "zh-Hans-orig": [fmt("json3", "a-zh-orig")],
        },
    }
    assert pick_caption_track(info) == ("a-zh-orig", "auto")


def test_original_language_auto_beats_manual_in_other_language():
    info = {
        "language": "zh-Hans",
        "subtitles": {"en": [fmt("json3", "m-en")]},
        "automatic_captions": {"zh-Hans-orig": [fmt("json3", "a-zh-orig")]},
    }
    assert pick_caption_track(info) == ("a-zh-orig", "auto")


def test_unknown_language_without_orig_uses_manual_en():
    info = {
        "subtitles": {"en": [fmt("json3", "m-en")], "de": [fmt("json3", "m-de")]},
        "automatic_captions": {"fr": [fmt("json3", "a-fr")]},
    }
    assert pick_caption_track(info) == ("m-en", "manual")


def test_no_tracks():
    assert pick_caption_track({"subtitles": {}, "automatic_captions": {}}) is None


def test_find_video_file(tmp_path):
    assert find_video_file(tmp_path) is None
    (tmp_path / "video.mp4.part").write_bytes(b"x")
    assert find_video_file(tmp_path) is None
    (tmp_path / "video.mp4.part-Frag3").write_bytes(b"x")
    (tmp_path / "video.mp4.temp").write_bytes(b"x")
    (tmp_path / "video.f137.mp4.ytdl").write_bytes(b"x")
    assert find_video_file(tmp_path) is None
    (tmp_path / "video.mp4").write_bytes(b"x")
    assert find_video_file(tmp_path) == tmp_path / "video.mp4"


def fake_ydl(*, extract_info=None, urlopen=None):
    class FakeYoutubeDL:
        def __init__(self, opts=None):
            self.opts = opts
            FakeYoutubeDL.last_opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            return extract_info(url, download)

        def sanitize_info(self, info):
            return info

        def urlopen(self, url):
            return urlopen(url)

    return FakeYoutubeDL


def test_fetch_captions_returns_none_when_fetch_fails(monkeypatch):
    def boom(url):
        raise OSError("network down")

    monkeypatch.setattr(youtube, "YoutubeDL", fake_ydl(urlopen=boom))
    info = {"language": "en", "subtitles": {"en": [fmt("json3", "http://x")]}}
    assert fetch_captions(info) is None


def test_fetch_captions_returns_none_on_bad_payload(monkeypatch):
    class Resp:
        def read(self):
            return b"not json"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(youtube, "YoutubeDL", fake_ydl(urlopen=lambda url: Resp()))
    info = {"language": "en", "subtitles": {"en": [fmt("json3", "http://x")]}}
    assert fetch_captions(info) is None


def test_fetch_info_ignores_missing_formats(monkeypatch):
    cls = fake_ydl(extract_info=lambda url, download: {"id": "x"})
    monkeypatch.setattr(youtube, "YoutubeDL", cls)
    assert fetch_info("dQw4w9WgXcQ") == {"id": "x"}
    assert cls.last_opts["ignore_no_formats_error"] is True


@pytest.mark.parametrize(
    "raw,code,message",
    [
        ("ERROR: [youtube] x: Private video. Sign in", "VIDEO_UNAVAILABLE", "这是私有视频，无法导入"),
        ("ERROR: This live event will begin in 2 hours", "LIVE_NOT_SUPPORTED", "不支持直播或尚未开始的直播"),
        ("ERROR: Premieres in 3 days", "LIVE_NOT_SUPPORTED", "不支持直播或尚未开始的直播"),
        (
            "ERROR: Video unavailable secret-detail",
            "VIDEO_UNAVAILABLE",
            "无法获取视频信息：视频可能已删除、需要会员或有地区限制",
        ),
    ],
)
def test_fetch_info_maps_download_errors(monkeypatch, raw, code, message):
    def boom(url, download):
        raise DownloadError(raw)

    monkeypatch.setattr(youtube, "YoutubeDL", fake_ydl(extract_info=boom))
    with pytest.raises(IngestError) as err:
        fetch_info("dQw4w9WgXcQ")
    assert (err.value.code, err.value.message) == (code, message)
    assert "secret-detail" not in err.value.message


@pytest.mark.parametrize("func,message", [(download_video, "视频下载失败"), (download_audio, "音频下载失败")])
@pytest.mark.parametrize("exc", [OSError("disk full"), DownloadError("ERROR: boom")])
def test_downloads_wrap_errors(monkeypatch, tmp_path, func, message, exc):
    def boom(url, download):
        raise exc

    monkeypatch.setattr(youtube, "YoutubeDL", fake_ydl(extract_info=boom))
    with pytest.raises(IngestError) as err:
        func("dQw4w9WgXcQ", tmp_path)
    assert err.value.code == "DOWNLOAD_FAILED"
    assert err.value.message == message
    assert err.value.__cause__ is exc


@pytest.mark.parametrize(
    "url",
    ["http://[::1", "https://www.youtube.com/watch?v=dQw4w9WgXcQ%0A", "https://youtu.be/dQw4w9WgXcQ%0A"],
)
def test_parse_video_id_rejects_malformed_and_newline_ids(url):
    with pytest.raises(IngestError) as err:
        parse_video_id(url)
    assert err.value.code == "INVALID_URL"
