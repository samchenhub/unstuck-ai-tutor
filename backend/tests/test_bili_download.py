import os

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
    kw.setdefault("workers", 1)  # deterministic call order; the parallel tests pass workers=4
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


BIG = bytes((i * 7 + i // 256) % 251 for i in range(10 * CHUNK))  # 10 chunks


class UrlOpener:
    """Thread-safe fake serving `data`; `rule(url, start)` -> None (serve), "fail" (OSError) or "partial"."""

    def __init__(self, rule=lambda url, start: None, data=BIG):
        self.rule, self.data, self.calls = rule, data, []

    def __call__(self, url, headers):
        start, end = parse_range(headers)
        self.calls.append((url, start))
        action = self.rule(url, start)
        if action == "fail":
            raise OSError("node down")
        body = self.data[start:end + 1]
        if action == "partial":
            body = body[:300]
        return 206, body, len(self.data)


def test_parallel_download_is_byte_identical(tmp_path):
    opener = UrlOpener()
    run(opener, tmp_path, workers=4)
    assert run.dest.read_bytes() == BIG
    assert sorted(start for _u, start in opener.calls) == [i * CHUNK for i in range(10)]
    assert opener.calls[0][1] == 0  # first request is sequential, to learn the total
    assert not run.dest.with_suffix(".mp4.part").exists()


def test_parallel_failing_chunk_refreshes_url_once(tmp_path):
    opener = UrlOpener(lambda url, start: "fail" if url == "A" and start == 3 * CHUNK else None)
    run(opener, tmp_path, workers=4)
    assert run.dest.read_bytes() == BIG
    assert run.fetched == ["A", "B"]
    assert run.sleeps == [2, 4]


def test_concurrent_failures_trigger_a_single_refresh(tmp_path):
    opener = UrlOpener(lambda url, start: "fail" if url == "A" and start in (2 * CHUNK, 3 * CHUNK) else None)
    run(opener, tmp_path, workers=4)
    assert run.dest.read_bytes() == BIG
    assert run.fetched == ["A", "B"]


def test_parallel_partial_reads_resume_within_the_chunk(tmp_path):
    opener = UrlOpener(lambda url, start: "partial" if start % CHUNK == 0 else None)
    run(opener, tmp_path, workers=4)
    assert run.dest.read_bytes() == BIG
    assert run.fetched == ["A"] and run.sleeps == []
    resumed = [start for _u, start in opener.calls if start % CHUNK]
    assert len(resumed) == 10 and all(start % CHUNK == 300 for start in resumed)


def test_parallel_persistent_chunk_failure_raises_and_cleans_up(tmp_path):
    opener = UrlOpener(lambda url, start: "fail" if start == 3 * CHUNK else None)
    with pytest.raises(IngestError) as err:
        run(opener, tmp_path, workers=4)
    assert err.value.code == "DOWNLOAD_FAILED"
    assert len(run.fetched) == 5
    assert not run.dest.exists()
    assert not run.dest.with_suffix(".mp4.part").exists()


class NodeOpener:
    """Serves `data`; `node(url)` -> max start offset (exclusive) that URL serves, else 403; `total(url)` -> reported size."""

    def __init__(self, data, node=lambda url: None, total=None):
        self.data, self.node, self.total, self.calls = data, node, total or (lambda url: len(data)), []

    def __call__(self, url, headers):
        start, end = parse_range(headers)
        self.calls.append((url, start))
        limit = self.node(url)
        if limit is not None and start >= limit:
            return 403, b"", None
        return 206, self.data[start:end + 1], self.total(url)


def test_refresh_budget_resets_after_steady_progress(tmp_path):
    data = bytes(i % 251 for i in range(28 * CHUNK))
    urls = "ABCDEFG"
    # Every node dies after serving 4 more chunks: 6 refreshes in all, never 5 without progress between them.
    opener = NodeOpener(data, node=lambda url: 4 * CHUNK * (urls.index(url) + 1))
    run(opener, tmp_path)
    assert run.dest.read_bytes() == data
    assert run.fetched == list(urls)


def test_refresh_budget_still_bounds_refreshes_without_progress(tmp_path):
    opener = NodeOpener(BIG, node=lambda url: CHUNK)  # one chunk (< 4) of progress, then 403 forever
    with pytest.raises(IngestError) as err:
        run(opener, tmp_path)
    assert err.value.code == "DOWNLOAD_FAILED"
    assert len(run.fetched) == 5
    assert not run.dest.exists()


def test_refreshed_url_with_different_size_is_a_failure(tmp_path):
    sizes = {"A": len(DATA), "B": len(DATA) + 1, "C": len(DATA)}
    opener = NodeOpener(DATA, node=lambda url: CHUNK if url == "A" else None, total=lambda url: sizes[url])
    run(opener, tmp_path)
    assert run.dest.read_bytes() == DATA
    assert run.fetched == ["A", "B", "C"]
    assert run.sleeps == [2, 4]  # B served the wrong file three times; nothing from it was written


def test_get_url_error_while_refreshing_counts_as_a_failure(tmp_path):
    opener = NodeOpener(DATA, node=lambda url: CHUNK if url == "A" else None)
    sleeps, calls = [], []

    def get_url():
        calls.append(1)
        if len(calls) == 2:
            raise IngestError("BILIBILI_BLOCKED", "blocked")
        return "A" if len(calls) == 1 else "B"

    dest = tmp_path / "video.mp4"
    download_ranges(get_url, dest, headers={}, opener=opener, sleep=sleeps.append, chunk_size=CHUNK, workers=1)
    assert dest.read_bytes() == DATA
    assert len(calls) == 3 and sleeps == [2]


def test_get_url_always_failing_is_bounded_and_reports_download_failed(tmp_path):
    opener = NodeOpener(DATA, node=lambda url: CHUNK)
    calls = []

    def get_url():
        calls.append(1)
        if len(calls) > 1:
            raise IngestError("BILIBILI_BLOCKED", "blocked")
        return "A"

    dest = tmp_path / "video.mp4"
    with pytest.raises(IngestError) as err:
        download_ranges(get_url, dest, headers={}, opener=opener, sleep=lambda _s: None, chunk_size=CHUNK, workers=1)
    assert err.value.code == "DOWNLOAD_FAILED"
    assert len(calls) == 5
    assert not dest.exists()


def test_short_pwrite_is_completed(tmp_path, monkeypatch):
    real = os.pwrite
    monkeypatch.setattr(os, "pwrite", lambda fd, data, pos: real(fd, bytes(data)[:100], pos))
    run(Opener(), tmp_path)
    assert run.dest.read_bytes() == DATA


def mirror_opener(slow=(), broken=(), delay=0.02, data=DATA):
    import time as _time

    calls = []

    def opener(url, headers):
        calls.append((url, headers["Range"]))
        if url in broken:
            return 503, b"", None
        if url in slow:
            _time.sleep(delay)
        start, end = parse_range(headers)
        return 206, data[start:end + 1], len(data)

    opener.calls = calls
    return opener


MANY = bytes(range(256)) * 80  # 20 chunks of 1024


def test_all_mirrors_download_at_once_and_the_fast_one_takes_more(tmp_path):
    opener = mirror_opener(slow={"slow"}, data=MANY)
    dest = download_ranges(lambda: ["slow", "fast"], tmp_path / "v.mp4", headers={}, chunk_size=CHUNK,
                           workers=2, opener=opener, sleep=lambda s: None)
    assert dest.read_bytes() == MANY
    used = [url for url, _r in opener.calls]
    assert used.count("fast") > used.count("slow") >= 1


def test_a_failing_mirror_is_set_aside_without_refreshing(tmp_path):
    refreshes = []

    def get_url():
        refreshes.append(1)
        return ["bad", "good"]

    opener = mirror_opener(broken={"bad"}, data=MANY)
    dest = download_ranges(get_url, tmp_path / "v.mp4", headers={}, chunk_size=CHUNK, workers=2,
                           opener=opener, sleep=lambda s: None)
    assert dest.read_bytes() == MANY
    assert len(refreshes) == 1
    assert sum(1 for url, _r in opener.calls if url == "bad") <= 4  # chunk-0 race + 3 strikes


def test_all_mirrors_failing_refreshes_the_list(tmp_path):
    lists = iter([["b1", "b2"], ["good"]])
    opener = mirror_opener(broken={"b1", "b2"})
    dest = download_ranges(lambda: next(lists), tmp_path / "v.mp4", headers={}, chunk_size=CHUNK,
                           workers=1, opener=opener, sleep=lambda s: None)
    assert dest.read_bytes() == DATA
    assert opener.calls[-1][0] == "good"


def test_progress_reports_the_contiguous_prefix(tmp_path):
    seen = []
    download_ranges(lambda: "A", tmp_path / "v.mp4", headers={}, chunk_size=CHUNK, workers=1,
                    opener=Opener(), sleep=lambda s: None, on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1024, 2560), (2048, 2560), (2560, 2560)]


def test_progress_waits_for_gaps(tmp_path):
    """A later chunk finishing first must not advance the prefix past a missing one."""
    seen = []
    download_ranges(lambda: ["m1", "m2"], tmp_path / "v.mp4", headers={}, chunk_size=CHUNK, workers=4,
                    opener=mirror_opener(slow={"m1"}, data=MANY), sleep=lambda s: None,
                    on_progress=lambda done, total: seen.append(done))
    assert seen == sorted(seen) and seen[-1] == len(MANY)
    assert all(done % CHUNK == 0 or done == len(MANY) for done in seen)
