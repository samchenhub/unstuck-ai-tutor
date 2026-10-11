import pytest

from unstuck.errors import IngestError
from unstuck.sources.bilibili.download import download_ranges

DATA = bytes(range(256)) * 10  # 2560 bytes = 2.5 chunks of 1024
CHUNK = 1024


def parse_range(headers):
    start, end = headers["Range"].removeprefix("bytes=").split("-")
    return int(start), int(end)


class Opener:
    """Serves DATA; `plan` maps call index -> override: an Exception to raise, an int status, or ("partial", n)."""

    def __init__(self, plan=None, data=DATA):
        self.plan, self.data, self.calls = plan or {}, data, []

    def __call__(self, url, headers):
        idx = len(self.calls)
        self.calls.append((url, headers))
        start, end = parse_range(headers)
        action = self.plan.get(idx)
        if isinstance(action, Exception):
            raise action
        if isinstance(action, int):
            return action, b"", None
        body = self.data[start:end + 1]
        if isinstance(action, tuple):
            body = body[:action[1]]
        return 206, body, len(self.data)


def run(opener, tmp_path, urls=("A", "B", "C", "D", "E", "F", "G"), **kw):
    sleeps, fetched = [], []

    def get_url():
        fetched.append(urls[len(fetched)])
        return fetched[-1]

    dest = tmp_path / "sub" / "video.mp4"
    kw.setdefault("chunk_size", CHUNK)
    try:
        result = download_ranges(get_url, dest, headers={"Referer": "x"}, opener=opener, sleep=sleeps.append, **kw)
    finally:
        run.sleeps, run.fetched, run.dest = sleeps, fetched, dest
    return result


def test_happy_path_downloads_all_chunks(tmp_path):
    opener = Opener()
    assert run(opener, tmp_path) == run.dest
    assert run.dest.read_bytes() == DATA
    assert not run.dest.with_suffix(".mp4.part").exists()
    assert [parse_range(h) for _u, h in opener.calls] == [(0, 1023), (1024, 2047), (2048, 3071)]
    assert all(h["Referer"] == "x" for _u, h in opener.calls)
    assert run.fetched == ["A"] and run.sleeps == []


def test_partial_chunk_keeps_bytes_and_resumes_from_new_offset(tmp_path):
    opener = Opener({0: ("partial", 300)})
    run(opener, tmp_path)
    assert run.dest.read_bytes() == DATA
    assert parse_range(opener.calls[1][1])[0] == 300
    assert run.sleeps == [] and run.fetched == ["A"]


def test_refreshes_url_after_consecutive_failures(tmp_path):
    opener = Opener({0: OSError("reset"), 1: TimeoutError(), 2: 503})
    run(opener, tmp_path)
    assert run.dest.read_bytes() == DATA
    assert run.sleeps == [2, 4]
    assert run.fetched == ["A", "B"]
    assert [u for u, _h in opener.calls[:3]] == ["A", "A", "A"]
    assert all(u == "B" for u, _h in opener.calls[3:])


def test_http_403_refreshes_immediately(tmp_path):
    opener = Opener({1: 403})
    run(opener, tmp_path)
    assert run.dest.read_bytes() == DATA
    assert run.sleeps == []
    assert run.fetched == ["A", "B"]


def test_persistent_failures_raise_and_leave_nothing(tmp_path):
    opener = Opener({i: OSError("down") for i in range(100)})
    with pytest.raises(IngestError) as err:
        run(opener, tmp_path)
    assert err.value.code == "DOWNLOAD_FAILED"
    assert not run.dest.exists()
    assert not run.dest.with_suffix(".mp4.part").exists()
    assert len(run.fetched) == 5  # first URL + max_refreshes(4)


def test_empty_body_counts_as_failure(tmp_path):
    opener = Opener({0: ("partial", 0)})
    run(opener, tmp_path)
    assert run.dest.read_bytes() == DATA
    assert run.sleeps == [2]
