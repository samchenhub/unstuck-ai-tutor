import pytest

from unstuck.errors import IngestError
from unstuck.sources.bilibili.client import BiliClient

NAV = {"code": -101, "data": {"isLogin": False, "wbi_img": {
    "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
    "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}


class HTTPStatusError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


class FakeFetch:
    def __init__(self, routes):
        self.routes = routes  # path fragment -> list of responses (dict or Exception), last one repeats
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        for fragment, responses in self.routes.items():
            if fragment in url:
                item = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(item, Exception):
                    raise item
                return item
        raise AssertionError(f"unexpected url {url}")


def make(routes, clock=lambda: 1702204169.0):
    sleeps = []
    client = BiliClient("chrome", fetch_json=FakeFetch(routes), sleep=sleeps.append, clock=clock)
    return client, sleeps


def test_signed_player_call_has_wbi_params():
    client, _ = make({"/x/web-interface/nav": [NAV], "/x/player/wbi/v2": [{"code": 0, "data": {"subtitle": {}}}]})
    client.player("BV1Ex4y1G7nG", 111)
    url = client._fetch_json.urls[-1]
    assert "/x/player/wbi/v2?" in url and "w_rid=" in url and "wts=1702204169" in url and "cid=111" in url


def test_backoff_on_risk_control_then_success():
    view = {"code": 0, "data": {"title": "t"}}
    client, sleeps = make({"/x/web-interface/view": [HTTPStatusError(412), HTTPStatusError(412), view]})
    assert client.view("BV1Ex4y1G7nG") == {"title": "t"}
    assert sleeps == [2.0, 4.0]


def test_backoff_exhausted_is_blocked():
    client, sleeps = make({"/x/web-interface/view": [HTTPStatusError(412)]})
    with pytest.raises(IngestError) as err:
        client.view("BV1Ex4y1G7nG")
    assert err.value.code == "BILIBILI_BLOCKED"
    assert sleeps == [2.0, 4.0]


@pytest.mark.parametrize("code,expected", [(-404, "VIDEO_UNAVAILABLE"), (62002, "VIDEO_UNAVAILABLE"),
                                           (-352, "BILIBILI_BLOCKED"), (-400, "BILIBILI_ERROR")])
def test_json_error_codes(code, expected):
    client, _ = make({"/x/web-interface/view": [{"code": code, "message": "x"}]})
    with pytest.raises(IngestError) as err:
        client.view("BV1Ex4y1G7nG")
    assert err.value.code == expected


def test_other_http_errors_are_not_retried():
    client, sleeps = make({"/x/web-interface/view": [HTTPStatusError(404)]})
    with pytest.raises(IngestError) as err:
        client.view("BV1Ex4y1G7nG")
    assert err.value.code == "BILIBILI_ERROR"
    assert sleeps == []


def test_signed_call_refreshes_key_once_on_failure():
    routes = {"/x/web-interface/nav": [NAV], "/x/player/wbi/playurl": [{"code": -403, "message": "x"}, {"code": 0, "data": {"durl": []}}]}
    client, _ = make(routes)
    assert client.playurl_mp4("BV1Ex4y1G7nG", 111) == {"durl": []}
    assert sum("/x/web-interface/nav" in u for u in client._fetch_json.urls) == 2


def test_login_state_is_cached():
    now = [1000.0]
    client, _ = make({"/x/web-interface/nav": [{"code": 0, "data": {"isLogin": True, "wbi_img": NAV["data"]["wbi_img"]}}]},
                     clock=lambda: now[0])
    assert client.is_logged_in() is True
    assert client.is_logged_in() is True
    assert sum("nav" in u for u in client._fetch_json.urls) == 1
    now[0] += 301
    client.is_logged_in()
    assert sum("nav" in u for u in client._fetch_json.urls) == 2


def test_login_check_failure_means_logged_out():
    client, _ = make({"/x/web-interface/nav": [HTTPStatusError(404)]})
    assert client.is_logged_in() is False


def test_ydl_options_use_browser_cookies():
    assert BiliClient("chrome").ydl_options()["cookiesfrombrowser"] == ("chrome",)
    assert "cookiesfrombrowser" not in BiliClient("").ydl_options()
