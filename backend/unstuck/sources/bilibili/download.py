"""Ranged MP4 downloader for Bilibili's CDN.

The CDN drops long single connections, and individual nodes behind a URL misbehave intermittently
(same-offset IncompleteRead, refused connections), so retrying one URL forever cannot succeed.
This downloader keeps partial bytes, resumes from the current offset, and fetches a fresh playback URL
(which usually lands on another node) after repeated failures. Speed differs a lot between the mirrors
Bilibili offers for one file (1.4 vs 12 MB/s measured side by side), so when given several it downloads
from all of them at once and the faster ones take more chunks. Chunks are queued in order and the
contiguous prefix on disk is reported, so a reader can start on the beginning before the end arrives.
"""

import http.client
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from unstuck.errors import IngestError

log = logging.getLogger(__name__)

_TIMEOUT_S = 30
_FAILURES = (OSError, urllib.error.URLError, TimeoutError, http.client.HTTPException)
_PROGRESS_CHUNKS = 4
_TOTAL = re.compile(r"/(\d+)\s*$")

Opener = Callable[[str, dict[str, str]], tuple[int, bytes, int | None]]


def _open_range(url: str, headers: dict[str, str]) -> tuple[int, bytes, int | None]:
    """One ranged GET -> (status, body, total size). A cut-off body is returned, not raised, to keep progress."""
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        return exc.code, b"", None
    with resp:
        content_range = resp.headers.get("Content-Range") or ""
        match = _TOTAL.search(content_range)
        total = int(match.group(1)) if match else None
        try:
            body = resp.read()
        except http.client.IncompleteRead as exc:
            body = exc.partial
        status = resp.status
    if total is None and status == 200:
        length = resp.headers.get("Content-Length")
        total = int(length) if length and length.isdigit() else None
    return status, body, total


def _fail() -> IngestError:
    return IngestError("DOWNLOAD_FAILED", "视频下载失败")


def _write_at(fd: int, data: bytes, pos: int) -> None:
    """`os.pwrite` may write less than asked; keep going until the whole buffer is on disk."""
    view = memoryview(data)
    while view:
        written = os.pwrite(fd, view, pos)
        view, pos = view[written:], pos + written


class _Download:
    """State shared by the chunk workers: the mirror list, total size, output fd and the in-order progress.

    Every worker is pinned to one mirror (round-robin), so faster mirrors finish more chunks from the shared
    queue. A mirror that keeps failing is set aside; when none is left (or one answers 403, i.e. the URLs
    expired) the list is refreshed. The refresh budget guards against URLs that never work, not against a long
    download: it is restored whenever `chunk_size * _PROGRESS_CHUNKS` bytes have been written since.
    """

    def __init__(self, get_url, fd, headers, chunk_size, attempts_per_url, max_refreshes, opener, sleep, on_progress):
        self.get_url, self.fd, self.headers, self.chunk_size = get_url, fd, headers, chunk_size
        self.attempts_per_url, self.max_refreshes, self.opener, self.sleep = attempts_per_url, max_refreshes, opener, sleep
        self.on_progress = on_progress
        self.lock = threading.Lock()
        self.abort = threading.Event()
        self.refreshes = 0
        self.progress = 0  # bytes written since the last refresh
        self.total: int | None = None
        self.prefix = 0  # bytes [0, prefix) are all on disk
        self.finished: set[int] = set()  # chunk starts at or beyond `prefix` that are complete
        self._slots = threading.local()
        self._next_slot = 0
        self.mirrors = self._fetch_mirrors()
        self.set_aside: set[str] = set()
        self.refreshing = False

    def _fetch_mirrors(self) -> list[str]:
        got = self.get_url()
        urls = [got] if isinstance(got, str) else [u for u in got if u]
        if not urls:
            raise _fail()
        return urls

    def url(self) -> str:
        """This worker's mirror (pinned on first use), skipping mirrors that were set aside."""
        with self.lock:
            slot = getattr(self._slots, "slot", None)
            if slot is None:
                slot = self._slots.slot = self._next_slot
                self._next_slot += 1
            usable = [m for m in self.mirrors if m not in self.set_aside] or self.mirrors
            return usable[slot % len(usable)]

    def give_up_on(self, url: str, *, expired: bool = False, own_refreshes: int = 0) -> bool:
        """Set a failing mirror aside; refresh the list when none is left or the URLs expired (403).

        Returns True if this call refreshed the list. Two budgets: the shared one (restored by progress) and
        the calling chunk's own, so one permanently bad offset cannot keep refreshing while others progress.
        The new URLs are fetched outside the lock, by one worker at a time.
        """
        with self.lock:
            if url not in self.mirrors or self.refreshing:
                return False  # another worker already refreshed the list, or is doing it now
            if not expired:
                self.set_aside.add(url)
                if any(m not in self.set_aside for m in self.mirrors):
                    log.warning("bilibili download: setting mirror %s aside", _host(url))
                    return False
            if self.refreshes >= self.max_refreshes or own_refreshes >= self.max_refreshes:
                raise _fail()
            self.refreshes += 1
            self.progress = 0
            self.refreshing = True
        try:
            mirrors = self._fetch_mirrors()
        except (IngestError, *_FAILURES) as exc:
            # No fresh URL right now (e.g. Bilibili blocked the request): count it as a failed attempt.
            log.warning("bilibili URL refresh failed: %s", exc)
            mirrors = None
            self.sleep(2)
        finally:
            with self.lock:
                self.refreshing = False
        if mirrors is not None:
            with self.lock:
                self.mirrors, self.set_aside = mirrors, set()
        return True

    def wrote(self, count: int) -> None:
        with self.lock:
            self.progress += count
            if self.progress >= self.chunk_size * _PROGRESS_CHUNKS:
                self.refreshes = self.progress = 0

    def completed(self, start: int) -> None:
        """Record a finished chunk and report the contiguous prefix if it grew."""
        with self.lock:
            self.finished.add(start)
            before = self.prefix
            while self.prefix in self.finished:
                self.finished.discard(self.prefix)
                self.prefix = min(self.prefix + self.chunk_size, self.total)
            prefix = self.prefix if self.prefix > before else None
        if prefix is not None and self.on_progress:
            self.on_progress(prefix, self.total)  # may arrive out of order across workers; consumers keep the max

    def first_chunk(self) -> int:
        """Fetch chunk 0 (it tells the total size); with several mirrors, race them and keep the first full reply."""
        if len(self.mirrors) > 1:
            won = self._race_first_chunk()
            if won is not None:
                return won
        return self.chunk(0)

    def _race_first_chunk(self) -> int | None:
        headers = {**self.headers, "Range": f"bytes=0-{self.chunk_size - 1}"}

        def fetch(url: str):
            try:
                status, body, total = self.opener(url, headers)
            except _FAILURES:
                return None
            full = total is not None and len(body) >= min(self.chunk_size, total)
            return (body, total) if status == 206 and full else None

        pool = ThreadPoolExecutor(max_workers=len(self.mirrors))
        try:
            for future in as_completed([pool.submit(fetch, url) for url in self.mirrors]):
                if (result := future.result()) is not None:
                    body, total = result
                    body = body[: self.chunk_size]
                    _write_at(self.fd, body, 0)
                    self.wrote(len(body))
                    self.total = total
                    return len(body)
        finally:
            pool.shutdown(wait=False)  # slower replies finish (or time out) in the background
        return None

    def chunk(self, start: int) -> int:
        """Fetch bytes [start, start + chunk_size) into the file, resuming after partial reads; returns end offset."""
        pos, failures, refreshes = start, 0, 0
        url = self.url()
        while not self.abort.is_set():
            total = self.total
            end = start + self.chunk_size if total is None else min(start + self.chunk_size, total)
            if pos >= end:
                return pos
            request_headers = {**self.headers, "Range": f"bytes={pos}-{start + self.chunk_size - 1}"}
            try:
                status, body, size = self.opener(url, request_headers)
            except _FAILURES as exc:
                log.warning("bilibili download error at byte %d: %s", pos, exc)
                status, body, size = 0, b"", None
            if size is not None and self.total is not None and size != self.total:
                # A mirror or refreshed URL must serve the same file; never mix bytes from a different one.
                log.warning("bilibili download size mismatch: %d != %d", size, self.total)
                status, body = 0, b""
            if status == 403:
                refreshes += self.give_up_on(url, expired=True, own_refreshes=refreshes)
                url, failures = self.url(), 0
                continue
            # A 200 means the server ignored Range and sent the whole file: only usable for the very first request.
            if status < 400 and body and not (status == 200 and pos):
                if status != 200:
                    body = body[:start + self.chunk_size - pos]
                _write_at(self.fd, body, pos)
                self.wrote(len(body))
                pos += len(body)
                failures = 0
                if self.total is None:
                    self.total = size if size is not None else (pos if status == 200 else None)
                continue
            failures += 1
            log.warning("bilibili download failure %d at byte %d (status %s)", failures, pos, status)
            if failures >= self.attempts_per_url:
                refreshes += self.give_up_on(url, own_refreshes=refreshes)
                url, failures = self.url(), 0
            else:
                self.sleep(min(2 ** failures, 8))
        return pos


def _host(url: str) -> str:
    return url.split("/")[2] if "//" in url else url


def download_ranges(
    get_url: Callable[[], str | list[str]],  # a URL, or mirrors of the same file (all are used at once)
    dest: Path,
    *,
    headers: dict[str, str],
    chunk_size: int = 1 << 20,
    attempts_per_url: int = 3,
    max_refreshes: int = 4,
    workers: int = 4,
    opener: Opener | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: Callable[[int, int], None] | None = None,  # (bytes from the start that are on disk, total)
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with part.open("wb") as out:
            dl = _Download(get_url, out.fileno(), headers, chunk_size, attempts_per_url, max_refreshes,
                           opener or _open_range, sleep, on_progress)
            written = dl.first_chunk()  # learns the total size from the first response
            total = dl.total
            if total is None:
                raise _fail()
            out.truncate(total)
            # Only whole chunks count: a cut-off reply that ignored Range leaves its last chunk to fetch again.
            first = total if written >= total else written // chunk_size * chunk_size
            for start in range(0, first, chunk_size):  # usually just chunk 0 (more if a server ignored Range)
                dl.completed(start)
            starts = range(first, total, chunk_size)
            if starts:
                _fetch_parallel(dl, starts, workers)
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest


def _fetch_parallel(dl: _Download, starts: range, workers: int) -> None:
    def fetch(start: int) -> None:
        dl.chunk(start)
        if not dl.abort.is_set():
            dl.completed(start)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(fetch, start) for start in starts]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            dl.abort.set()
            for future in futures:
                future.cancel()
            raise
