import hashlib

import pytest

from unstuck.errors import IngestError
from unstuck.models import Cue
from unstuck.sources.base import Part, Playback, VideoMeta
from unstuck.sources.bilibili import parse

BV = "BV1Ex4y1G7nG"
VIEW = {
    "bvid": BV, "title": "导数1：导数的概念", "pic": "http://i0.hdslb.com/bfs/archive/a.jpg",
    "owner": {"name": "数学老师"}, "duration": 1500,
    "pages": [
        {"cid": 111, "page": 1, "part": "导数的概念", "duration": 1272},
        {"cid": 222, "page": 2, "part": "求导法则", "duration": 228},
    ],
}


@pytest.mark.parametrize("url,expected", [
    (f"https://www.bilibili.com/video/{BV}", (BV, 1)),
    (f"https://www.bilibili.com/video/{BV}/?spm_id_from=333&p=3", (BV, 3)),
    (f"https://m.bilibili.com/video/{BV}?p=2", (BV, 2)),
    (f"bilibili.com/video/{BV}", (BV, 1)),
])
def test_parse_url(url, expected):
    assert parse.parse_url(url) == expected


@pytest.mark.parametrize("url,code", [
    ("https://www.bilibili.com/cheese/play/ss607570738", "UNSUPPORTED_URL"),
    ("https://www.bilibili.com/bangumi/play/ep1", "UNSUPPORTED_URL"),
    ("https://space.bilibili.com/123456", "UNSUPPORTED_URL"),
    ("https://www.bilibili.com/list/watchlater", "UNSUPPORTED_URL"),
    ("https://www.bilibili.com/video/av989935671", "INVALID_URL"),
    (f"https://www.bilibili.com/video/{BV}?p=0", "INVALID_URL"),
    (f"https://www.bilibili.com/video/{BV}?p=%C2%B2", "INVALID_URL"),
    ("https://www.bilibili.com/", "INVALID_URL"),
])
def test_parse_url_rejects(url, code):
    with pytest.raises(IngestError) as err:
        parse.parse_url(url)
    assert err.value.code == code


def test_video_ids():
    assert parse.make_video_id(BV, 3) == f"{BV}_p3"
    assert parse.split_video_id(f"{BV}_p3") == (BV, 3)
    assert parse.watch_url(BV, 3) == f"https://www.bilibili.com/video/{BV}?p=3"
    with pytest.raises(ValueError):
        parse.split_video_id("dQw4w9WgXcQ")


def test_hosts():
    assert parse.is_bilibili_host("www.bilibili.com")
    assert parse.is_bilibili_host("b23.tv")
    assert not parse.is_bilibili_host("notbilibili.com")


def test_wbi_matches_published_example():
    key = parse.mixin_key(
        "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
        "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
    )
    assert key == "ea1db124af3c7062474693fa704f4ff8"
    signed = parse.wbi_sign({"foo": "114", "bar": "514", "zab": 1919810}, key, 1702204169)
    assert signed["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4"
    assert signed["wts"] == "1702204169"


def test_wbi_filters_reserved_characters():
    signed = parse.wbi_sign({"q": "a!b'c(d)e*"}, "k", 1)
    assert signed["q"] == "abcde"
    assert signed["w_rid"] == hashlib.md5(b"q=abcde&wts=1k").hexdigest()


def test_info_and_meta_from_view():
    info = parse.info_from_view(VIEW, BV, 2)
    assert info == {
        "source": "bilibili", "bvid": BV, "part": 2, "cid": 222, "title": "导数1：导数的概念",
        "channel": "数学老师", "duration": 228.0, "part_title": "求导法则", "parts_total": 2,
        "thumbnail": "https://i0.hdslb.com/bfs/archive/a.jpg",
    }
    assert parse.meta_from_info(info, 7200) == VideoMeta(
        title="导数1：导数的概念", channel="数学老师", duration=228.0, part_title="求导法则",
        parts_total=2, thumbnail="https://i0.hdslb.com/bfs/archive/a.jpg",
    )


def test_single_part_has_no_part_title():
    view = {**VIEW, "pages": [VIEW["pages"][0]]}
    assert parse.info_from_view(view, BV, 1)["part_title"] == ""


def test_missing_part_and_too_long():
    with pytest.raises(IngestError) as err:
        parse.info_from_view(VIEW, BV, 9)
    assert err.value.code == "VIDEO_UNAVAILABLE"
    with pytest.raises(IngestError) as err:
        parse.meta_from_info(parse.info_from_view(VIEW, BV, 1), 600)
    assert err.value.code == "TOO_LONG"


def test_pick_subtitle_prefers_uploader_chinese():
    subs = [
        {"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/ai.json"},
        {"lan": "zh-CN", "subtitle_url": "//i0.hdslb.com/human.json"},
        {"lan": "en-US", "subtitle_url": "//i0.hdslb.com/en.json"},
    ]
    assert parse.pick_subtitle_url(subs) == "https://i0.hdslb.com/human.json"
    assert parse.pick_subtitle_url(subs[:1]) == "https://aisubtitle.hdslb.com/ai.json"
    assert parse.pick_subtitle_url([{"lan": "ai-zh", "subtitle_url": ""}]) is None
    assert parse.pick_subtitle_url(subs[2:]) is None  # non-Chinese tracks fall back to Whisper
    assert parse.pick_subtitle_url([{"lan": "ja", "subtitle_url": "//i0.hdslb.com/ja.json"}]) is None
    assert parse.pick_subtitle_url([]) is None


def test_subtitle_to_cues():
    body = [{"from": 0, "to": 1.5, "content": " 大家好 "}, {"from": 2, "to": 3, "content": ""}]
    assert parse.subtitle_to_cues(body) == [Cue(0.0, 1.5, "大家好")]


@pytest.mark.parametrize("last_end,ok", [(1271.1, True), (1100.0, True), (791.78, False), (1290.0, False)])
def test_subtitle_matches(last_end, ok):
    cues = [Cue(0.0, 2.0, "a"), Cue(last_end - 1, last_end, "b")]
    assert parse.subtitle_matches(cues, 1272.0) is ok


def test_subtitle_matches_empty():
    assert parse.subtitle_matches([], 100.0) is False


def test_mp4_playback():
    data = {"durl": [{"url": "https://upos.bilivideo.com/x.mp4?deadline=1791671421&upsig=a"}]}
    assert parse.mp4_playback(data, 1272.0, now=0) == Playback(
        kind="mp4", url="https://upos.bilivideo.com/x.mp4?deadline=1791671421&upsig=a",
        expires_at=1791671421.0, duration=1272.0,
    )
    no_deadline = {"durl": [{"url": "https://upos.bilivideo.com/x.mp4"}]}
    assert parse.mp4_playback(no_deadline, 10.0, now=100.0).expires_at == 100.0 + 3600
    with pytest.raises(IngestError) as err:
        parse.mp4_playback({"durl": []}, 10.0, now=0)
    assert err.value.code == "PLAYBACK_UNAVAILABLE"


def test_mp4_playback_upgrades_insecure_urls():
    http = {"durl": [{"url": "http://upos.bilivideo.com/x.mp4?deadline=5"}]}
    assert parse.mp4_playback(http, 10.0, now=0).url == "https://upos.bilivideo.com/x.mp4?deadline=5"
    relative = {"durl": [{"url": "//upos.bilivideo.com/x.mp4"}]}
    assert parse.mp4_playback(relative, 10.0, now=0).url == "https://upos.bilivideo.com/x.mp4"


def test_mp4_playback_ignores_non_ascii_deadline():
    data = {"durl": [{"url": "https://upos.bilivideo.com/x.mp4?deadline=%C2%B2"}]}
    assert parse.mp4_playback(data, 10.0, now=100.0).expires_at == 100.0 + 3600


def test_parts_from_view():
    assert parse.parts_from_view(VIEW, BV) == [
        Part(part=1, title="导数的概念", duration=1272.0, video_id=f"{BV}_p1"),
        Part(part=2, title="求导法则", duration=228.0, video_id=f"{BV}_p2"),
    ]


def test_dash_audio_url_picks_the_smallest_track():
    data = {"dash": {"audio": [
        {"id": 30280, "bandwidth": 190000, "baseUrl": "https://upos.bilivideo.com/a-192.m4s"},
        {"id": 30216, "bandwidth": 66000, "base_url": "http://upos.bilivideo.com/a-64.m4s"},
        {"id": 30232, "bandwidth": 130000, "baseUrl": "https://upos.bilivideo.com/a-132.m4s"},
    ]}}
    assert parse.dash_audio_url(data) == "https://upos.bilivideo.com/a-64.m4s"
    assert parse.dash_audio_url({"durl": [{"url": "x"}]}) is None
    assert parse.dash_audio_url({"dash": {"audio": None}}) is None
