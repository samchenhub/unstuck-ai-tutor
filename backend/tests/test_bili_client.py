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
    client, sleeps = make({"/x/web-interface/nav": [NAV], "/x/web-interface/wbi/view": [HTTPStatusError(412), HTTPStatusError(412), view]})
    assert client.view("BV1Ex4y1G7nG") == {"title": "t"}
    assert sleeps == [2.0, 4.0]


def test_backoff_exhausted_is_blocked():
    client, sleeps = make({"/x/web-interface/nav": [NAV], "/x/web-interface/wbi/view": [HTTPStatusError(412)]})
    with pytest.raises(IngestError) as err:
        client.view("BV1Ex4y1G7nG")
    assert err.value.code == "BILIBILI_BLOCKED"
    assert sleeps == [2.0, 4.0]


@pytest.mark.parametrize("code,expected", [(-404, "VIDEO_UNAVAILABLE"), (62002, "VIDEO_UNAVAILABLE"),
                                           (-352, "BILIBILI_BLOCKED"), (-400, "BILIBILI_ERROR")])
def test_json_error_codes(code, expected):
    client, _ = make({"/x/web-interface/nav": [NAV], "/x/web-interface/wbi/view": [{"code": code, "message": "x"}]})
    with pytest.raises(IngestError) as err:
        client.view("BV1Ex4y1G7nG")
    assert err.value.code == expected


def test_other_http_errors_are_not_retried():
    client, sleeps = make({"/x/web-interface/nav": [NAV], "/x/web-interface/wbi/view": [HTTPStatusError(404)]})
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


def test_unreadable_cookies_degrade_to_anonymous(monkeypatch):
    import json

    from yt_dlp.cookies import CookieLoadError

    built = []

    class Resp:
        def __init__(self, url):
            self.body = NAV if "/nav" in url else {"code": 0, "data": {"title": "t"}}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.body).encode()

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            built.append(opts)

        def urlopen(self, url):
            if "cookiesfrombrowser" in self.opts:
                raise CookieLoadError("failed to load cookies")
            return Resp(url)

    monkeypatch.setattr("unstuck.sources.bilibili.client.YoutubeDL", FakeYDL)
    client = BiliClient("chrome")
    assert client.view("BV1Ex4y1G7nG") == {"title": "t"}
    assert client.cookies_unavailable is True
    assert "cookiesfrombrowser" not in client.ydl_options()
    assert len(built) == 2 and "cookiesfrombrowser" in built[0] and "cookiesfrombrowser" not in built[1]


def test_nav_without_wbi_keys_is_blocked_not_a_crash():
    client, _ = make({"/x/web-interface/nav": [{"code": -352, "data": {}}]})
    with pytest.raises(IngestError) as err:
        client.playurl_mp4("BV1Ex4y1G7nG", 111)
    assert err.value.code == "BILIBILI_BLOCKED"


def test_nav_without_wbi_keys_otherwise_is_error():
    client, _ = make({"/x/web-interface/nav": [{}]})
    with pytest.raises(IngestError) as err:
        client.player("BV1Ex4y1G7nG", 111)
    assert err.value.code == "BILIBILI_ERROR"


def test_signed_call_does_not_refresh_after_exhausted_backoff():
    client, sleeps = make({"/x/web-interface/nav": [NAV], "/x/player/wbi/playurl": [HTTPStatusError(412)]})
    with pytest.raises(IngestError) as err:
        client.playurl_mp4("BV1Ex4y1G7nG", 111)
    assert err.value.code == "BILIBILI_BLOCKED"
    assert sleeps == [2.0, 4.0]
    assert sum("/x/web-interface/nav" in u for u in client._fetch_json.urls) == 1


def test_transport_errors_are_retried_but_plain_oserror_is_not():
    from yt_dlp.networking.exceptions import TransportError

    view = {"code": 0, "data": {"title": "t"}}
    client, sleeps = make({"/x/web-interface/nav": [NAV], "/x/web-interface/wbi/view": [TransportError("reset"), view]})
    assert client.view("BV1Ex4y1G7nG") == {"title": "t"}
    assert sleeps == [2.0]
    client, sleeps = make({"/x/web-interface/nav": [NAV], "/x/web-interface/wbi/view": [OSError("disk")]})
    with pytest.raises(IngestError) as err:
        client.view("BV1Ex4y1G7nG")
    assert err.value.code == "BILIBILI_ERROR" and sleeps == []


def test_forced_login_check_bypasses_cache_and_drops_cookie_jar():
    logged_in = {"code": 0, "data": {"isLogin": True, "wbi_img": NAV["data"]["wbi_img"]}}
    client, _ = make({"/x/web-interface/nav": [NAV, logged_in]})
    assert client.is_logged_in() is False
    client._ydl = object()
    assert client.is_logged_in() is False  # still cached
    assert client.is_logged_in(force=True) is True
    assert client._ydl is None


def test_check_login_trusts_cached_login_and_rechecks_logout():
    logged_in = {"code": 0, "data": {"isLogin": True, "wbi_img": NAV["data"]["wbi_img"]}}
    client, _ = make({"/x/web-interface/nav": [NAV, logged_in]})
    assert client.check_login() is False
    assert client.check_login() is True  # logged out is re-checked: the user may have just logged in
    client._ydl = sentinel = object()
    assert client.check_login() is True  # a cached login is trusted, cookies are not re-read
    assert client._ydl is sentinel


def test_check_login_does_not_reread_unreadable_cookies():
    client, _ = make({"/x/web-interface/nav": [NAV, NAV]})
    assert client.check_login() is False
    client.cookies_unavailable = True  # e.g. the macOS keychain prompt was refused
    assert client.check_login() is False
    assert client.cookies_unavailable is True


def test_video_info_uses_the_signed_endpoint():
    """Bilibili answers 412 to the unsigned view endpoint when logged out; the WBI-signed one works."""
    fetch = FakeFetch({"/x/web-interface/nav": [NAV], "/x/web-interface/wbi/view": [{"code": 0, "data": {"title": "t"}}]})
    client = BiliClient("", fetch_json=fetch, sleep=lambda s: None, clock=lambda: 1702204169.0)
    assert client.view("BV1Ex4y1G7nG") == {"title": "t"}
    url = [u for u in fetch.urls if "/wbi/view" in u][0]
    assert "bvid=BV1Ex4y1G7nG" in url and "w_rid=" in url and "wts=" in url
