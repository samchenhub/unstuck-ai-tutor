import http.cookiejar
import time

import pytest

from unstuck.sources.bilibili import login

SUCCESS_URL = ("https://passport.biligame.com/x/passport-login/web/crossDomain?DedeUserID=12345&DedeUserID__ckMd5=abcd"
               "&Expires=1800000000&SESSDATA=sess%2Cvalue%2Cxyz&bili_jct=csrf123&gourl=https%3A%2F%2Fwww.bilibili.com")


class FakePassport:
    def __init__(self, polls):
        self.polls, self.urls = list(polls), []

    def __call__(self, url):
        self.urls.append(url)
        if url.endswith("/generate"):
            return {"code": 0, "data": {"url": "https://account.bilibili.com/h5/account-h5/auth/scan-web?qrcode_key=k1",
                                        "qrcode_key": "k1"}}
        if "/poll" not in url:
            return {}  # opening the cross-domain URL sets nothing here
        code, url_value = self.polls.pop(0)
        return {"code": 0, "data": {"code": code, "message": "", "url": url_value, "refresh_token": "", "timestamp": 1}}


def test_start_returns_a_qr_code_for_the_login_url():
    started = login.start(fetch=FakePassport([]))
    assert started.key == "k1"
    assert started.qr.startswith("data:image/svg+xml") and len(started.qr) > 200


def test_poll_reports_each_state_and_saves_the_cookies_on_success(tmp_path):
    cookie_file = tmp_path / "bilibili_cookies.txt"
    passport = FakePassport([(86101, ""), (86090, ""), (0, SUCCESS_URL)])
    assert login.poll("k1", cookie_file, fetch=passport) == "waiting"
    assert login.poll("k1", cookie_file, fetch=passport) == "scanned"
    assert not cookie_file.exists()
    assert login.poll("k1", cookie_file, fetch=passport) == "done"
    assert "qrcode_key=k1" in passport.urls[-1]

    jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    jar.load()
    cookies = {c.name: c for c in jar}
    assert cookies["SESSDATA"].value == "sess%2Cvalue%2Cxyz"  # kept URL-encoded, as Bilibili's own cookie is
    assert cookies["bili_jct"].value == "csrf123" and cookies["DedeUserID"].value == "12345"
    assert cookies["SESSDATA"].domain == ".bilibili.com" and cookies["SESSDATA"].expires == 1800000000


def test_an_expired_code_and_a_login_without_cookies(tmp_path):
    assert login.poll("k1", tmp_path / "c.txt", fetch=FakePassport([(86038, "")])) == "expired"
    assert login.poll("k1", tmp_path / "c.txt", fetch=FakePassport([(0, "https://x/?gourl=y")])) == "failed"
    assert not (tmp_path / "c.txt").exists()


def test_logout_removes_the_cookies(tmp_path):
    cookie_file = tmp_path / "bilibili_cookies.txt"
    login.poll("k1", cookie_file, fetch=FakePassport([(0, SUCCESS_URL)]))
    login.logout(cookie_file)
    assert not cookie_file.exists()
    login.logout(cookie_file)  # nothing to remove is fine


def test_missing_expiry_defaults_to_months_ahead(tmp_path):
    cookie_file = tmp_path / "c.txt"
    url = SUCCESS_URL.replace("&Expires=1800000000", "")
    login.poll("k1", cookie_file, fetch=FakePassport([(0, url)]))
    jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    jar.load()
    assert all(c.expires > time.time() + 30 * 86400 for c in jar)


def test_the_client_prefers_the_saved_login_over_the_browser(tmp_path):
    from unstuck.sources.bilibili.client import BiliClient

    cookie_file = tmp_path / "bilibili_cookies.txt"
    client = BiliClient("chrome", cookie_file=cookie_file)
    assert client.ydl_options()["cookiesfrombrowser"] == ("chrome",)
    login.poll("k1", cookie_file, fetch=FakePassport([(0, SUCCESS_URL)]))
    opts = client.ydl_options()
    assert opts["cookiefile"] == str(cookie_file) and "cookiesfrombrowser" not in opts


def test_unknown_poll_codes_are_errors():
    with pytest.raises(Exception):
        login.poll("k1", None, fetch=lambda url: {"code": -400, "message": "bad request"})


def test_network_problems_become_bilibili_errors(monkeypatch):
    import urllib.error

    from unstuck.errors import IngestError

    class Down:
        def open(self, request, timeout):
            raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(login.urllib.request, "build_opener", lambda *handlers: Down())
    with pytest.raises(IngestError) as err:
        login.start()
    assert err.value.code == "BILIBILI_ERROR"
    with pytest.raises(IngestError):
        login.start(fetch=lambda url: {"code": 0, "data": {}})  # no url / key in the reply


def test_cookies_set_by_the_poll_response_are_saved(tmp_path):
    """Bilibili may set the login only through Set-Cookie on the poll reply (no values in data.url)."""
    cookie_file = tmp_path / "c.txt"

    def fetch(url):
        return login.Reply(
            {"code": 0, "data": {"code": 0, "url": "https://passport.biligame.com/crossDomain?gourl=https%3A%2F%2Fwww.bilibili.com"}},
            {"SESSDATA": "abc%2C123", "bili_jct": "csrf", "DedeUserID": "42"},
            1800000000,
        )

    assert login.poll("k1", cookie_file, fetch=fetch) == "done"
    jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    jar.load()
    assert {c.name: c.value for c in jar}["SESSDATA"] == "abc%2C123"


def test_the_cross_domain_url_is_followed_when_nothing_else_carried_the_login(tmp_path):
    cookie_file = tmp_path / "c.txt"
    visited = []

    def fetch(url):
        visited.append(url)
        if "/poll" in url:
            return login.Reply({"code": 0, "data": {"code": 0, "url": "https://passport.biligame.com/crossDomain?x=1"}}, {}, None)
        return login.Reply({}, {"SESSDATA": "from-redirect", "bili_jct": "j"}, None)

    assert login.poll("k1", cookie_file, fetch=fetch) == "done"
    assert visited[-1] == "https://passport.biligame.com/crossDomain?x=1"
    assert "from-redirect" in cookie_file.read_text(encoding="utf-8")


def test_a_failed_login_logs_which_cookie_names_it_saw_not_their_values(tmp_path, caplog):
    def fetch(url):
        if "/poll" in url:
            return login.Reply({"code": 0, "data": {"code": 0, "url": "https://x/?gourl=y&bili_jct=secret"}}, {"buvid3": "v"}, None)
        return login.Reply({}, {}, None)

    assert login.poll("k1", tmp_path / "c.txt", fetch=fetch) == "failed"
    assert "bili_jct" in caplog.text and "buvid3" in caplog.text and "secret" not in caplog.text
