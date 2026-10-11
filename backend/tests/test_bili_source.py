from unstuck.models import Cue
from unstuck.sources.bilibili.client import BiliClient
from unstuck.sources.bilibili.source import BilibiliSource
from tests.test_bili_client import NAV, FakeFetch

BV = "BV1Ex4y1G7nG"
VIEW = {"code": 0, "data": {"bvid": BV, "title": "导数", "pic": "http://i0.hdslb.com/a.jpg", "owner": {"name": "UP"},
                            "pages": [{"cid": 111, "page": 1, "part": "概念", "duration": 100},
                                      {"cid": 222, "page": 2, "part": "法则", "duration": 50}]}}
PLAYER = {"code": 0, "data": {"subtitle": {"subtitles": [{"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/s.json"}]}}}
GOOD_SUB = {"body": [{"from": 0, "to": 2, "content": "大家好"}, {"from": 90, "to": 99, "content": "再见"}]}
WRONG_SUB = {"body": [{"from": 0, "to": 2, "content": "去京都"}, {"from": 30, "to": 40, "content": "米其林"}]}
PLAYURL = {"code": 0, "data": {"durl": [{"url": "https://upos.bilivideo.com/v.mp4?deadline=1791671421"}]}}


def source(routes):
    return BilibiliSource(BiliClient("chrome", fetch_json=FakeFetch(routes), sleep=lambda s: None, clock=lambda: 1.0e9))


def base_routes(sub):
    return {"/x/web-interface/nav": [NAV], "/x/web-interface/view": [VIEW], "/x/player/wbi/v2": [PLAYER],
            "aisubtitle.hdslb.com": [sub], "/x/player/wbi/playurl": [PLAYURL]}


def test_fetch_info_and_check():
    src = source(base_routes(GOOD_SUB))
    info = src.fetch_info(f"{BV}_p1")
    assert (info["cid"], info["duration"], info["parts_total"]) == (111, 100.0, 2)
    assert src.check_info(info, 7200).part_title == "概念"


def test_captions_from_ai_subtitles():
    src = source(base_routes(GOOD_SUB))
    cues = src.fetch_captions(src.fetch_info(f"{BV}_p1"))
    assert cues == [Cue(0.0, 2.0, "大家好"), Cue(90.0, 99.0, "再见")]


def test_mismatched_subtitles_are_rejected():
    src = source(base_routes(WRONG_SUB))
    assert src.fetch_captions(src.fetch_info(f"{BV}_p1")) is None


def test_no_subtitles_and_errors_return_none():
    routes = base_routes(GOOD_SUB)
    routes["/x/player/wbi/v2"] = [{"code": 0, "data": {"subtitle": {"subtitles": []}}}]
    src = source(routes)
    assert src.fetch_captions(src.fetch_info(f"{BV}_p1")) is None
    routes["/x/player/wbi/v2"] = [RuntimeError("network down")]
    assert source(routes).fetch_captions({"bvid": BV, "cid": 111, "duration": 100.0}) is None


def test_playback_and_parts():
    src = source(base_routes(GOOD_SUB))
    pb = src.playback(f"{BV}_p2")
    assert (pb.kind, pb.url, pb.duration) == ("mp4", "https://upos.bilivideo.com/v.mp4?deadline=1791671421", 50.0)
    assert [p.video_id for p in src.parts(f"{BV}_p2")] == [f"{BV}_p1", f"{BV}_p2"]


def test_audio_and_video_share_one_mp4_download(monkeypatch, tmp_path):
    seen = []

    def opener(url, headers):
        seen.append((url, headers))
        start = int(headers["Range"].removeprefix("bytes=").split("-")[0])
        return 206, b"mp4data"[start:], 7

    monkeypatch.setattr("unstuck.sources.bilibili.download._open_range", opener)
    src = source(base_routes(GOOD_SUB))
    out = tmp_path / "vid"
    assert src.download_audio(f"{BV}_p2", out) == out / "video.mp4"
    assert (out / "video.mp4").read_bytes() == b"mp4data"
    n = len(seen)
    assert n >= 1
    assert seen[0][0] == "https://upos.bilivideo.com/v.mp4?deadline=1791671421"
    assert seen[0][1]["Referer"] == "https://www.bilibili.com/"
    assert src.download_video(f"{BV}_p2", out) == out / "video.mp4"
    assert len(seen) == n  # reused, no second download
