import pytest

from unstuck import sources
from unstuck.errors import IngestError
from unstuck.ingest import youtube as yt
from unstuck.models import Video
from unstuck.sources.base import Playback, VideoMeta


def make_video(video_id="dQw4w9WgXcQ", source="youtube"):
    return Video(
        id=video_id, url="u", title="", channel="", duration=0.0, status="ready", ask_ready=True,
        stage_states={}, visual_mode="full", outline=None, error_code=None, error_message=None,
        created_at="", source=source,
    )


def test_resolve_youtube(settings):
    ref = sources.resolve("https://youtu.be/dQw4w9WgXcQ", settings)
    assert (ref.source, ref.video_id, ref.url) == ("youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_resolve_rejects_garbage(settings):
    with pytest.raises(IngestError) as err:
        sources.resolve("https://vimeo.com/1", settings)
    assert err.value.code == "INVALID_URL"


def test_for_video_youtube_adapter_delegates(monkeypatch, settings):
    src = sources.for_video(make_video(), settings)
    assert src.name == "youtube"
    monkeypatch.setattr(yt, "fetch_info", lambda vid: {"id": vid, "title": "T", "channel": "C", "duration": 12})
    info = src.fetch_info("dQw4w9WgXcQ")
    assert src.check_info(info, 7200) == VideoMeta(title="T", channel="C", duration=12.0)
    assert src.playback("dQw4w9WgXcQ") == Playback(kind="youtube")
    assert [p.video_id for p in src.parts("dQw4w9WgXcQ")] == ["dQw4w9WgXcQ"]


def test_for_name_unknown(settings):
    with pytest.raises(ValueError):
        sources.for_name("vimeo", settings)


def test_resolve_bilibili_links(settings):
    ref = sources.resolve("https://www.bilibili.com/video/BV1Ex4y1G7nG/?p=3", settings)
    assert (ref.source, ref.video_id, ref.course_id, ref.part) == ("bilibili", "BV1Ex4y1G7nG_p3", "BV1Ex4y1G7nG", 3)
    assert ref.url == "https://www.bilibili.com/video/BV1Ex4y1G7nG?p=3"
    assert sources.resolve("BV1Ex4y1G7nG", settings).video_id == "BV1Ex4y1G7nG_p1"


def test_resolve_b23_short_link(settings):
    seen = []

    def follow(url):
        seen.append(url)
        return "https://www.bilibili.com/video/BV1Ex4y1G7nG?p=2&share_source=copy"

    ref = sources.resolve("https://b23.tv/AbCdEf", settings, follow_redirect=follow)
    assert ref.video_id == "BV1Ex4y1G7nG_p2" and seen == ["https://b23.tv/AbCdEf"]


def test_resolve_bilibili_errors(settings):
    with pytest.raises(IngestError) as err:
        sources.resolve("https://www.bilibili.com/cheese/play/ss1", settings)
    assert err.value.code == "UNSUPPORTED_URL"
    with pytest.raises(IngestError) as err:
        sources.resolve("https://example.com/x", settings)
    assert err.value.code == "INVALID_URL" and "B站" in err.value.message


def test_for_name_bilibili(settings):
    assert sources.for_name("bilibili", settings).name == "bilibili"


def test_b23_redirect_to_a_non_bilibili_host_is_rejected(settings):
    with pytest.raises(IngestError) as err:
        sources.resolve("https://b23.tv/x", settings, follow_redirect=lambda u: "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert err.value.code == "INVALID_URL"


def test_follow_redirect_reads_location_without_following(monkeypatch):
    import io
    import urllib.error

    from unstuck.sources import _short_link_target

    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.get_method(), request.full_url))
            raise urllib.error.HTTPError(request.full_url, 302, "Found",
                                         {"Location": "https://www.bilibili.com/video/BV1Ex4y1G7nG?p=2"}, io.BytesIO())

    monkeypatch.setattr(sources, "_no_redirect_opener", lambda: Opener())
    assert _short_link_target("https://b23.tv/AbCdEf") == "https://www.bilibili.com/video/BV1Ex4y1G7nG?p=2"
    assert calls == [("HEAD", "https://b23.tv/AbCdEf")]


def test_short_link_without_redirect_is_invalid(monkeypatch):
    from unstuck.sources import _short_link_target

    class Opener:
        def open(self, request, timeout):
            class Resp:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return Resp()

    monkeypatch.setattr(sources, "_no_redirect_opener", lambda: Opener())
    with pytest.raises(IngestError) as err:
        _short_link_target("https://b23.tv/AbCdEf")
    assert err.value.code == "INVALID_URL"


def test_resolve_app_share_text(settings):
    seen = []

    def follow(url):
        seen.append(url)
        return "https://www.bilibili.com/video/BV1Ex4y1G7nG"

    ref = sources.resolve("【导数全讲解-哔哩哔哩】 https://b23.tv/AbCdEf1", settings, follow_redirect=follow)
    assert ref.video_id == "BV1Ex4y1G7nG_p1" and seen == ["https://b23.tv/AbCdEf1"]
    ref = sources.resolve("看这个https://www.bilibili.com/video/BV1Ex4y1G7nG?p=3分享自哔哩哔哩", settings)
    assert ref.video_id == "BV1Ex4y1G7nG_p3"
    assert sources.resolve("复制这个 BV1Ex4y1G7nG 打开", settings).video_id == "BV1Ex4y1G7nG_p1"


def test_bilibili_status_uses_check_login(settings, monkeypatch):
    class Client:
        def check_login(self):
            return True

    monkeypatch.setattr(sources, "client_for", lambda s: Client())
    assert sources.bilibili_status(settings) == {"logged_in": True, "cookies_browser": settings.bilibili_cookies_browser}


def test_plain_links_without_scheme_keep_their_part(settings):
    assert sources.resolve("www.bilibili.com/video/BV1Ex4y1G7nG?p=3", settings).video_id == "BV1Ex4y1G7nG_p3"
    assert sources.resolve("youtu.be/dQw4w9WgXcQ", settings).video_id == "dQw4w9WgXcQ"
