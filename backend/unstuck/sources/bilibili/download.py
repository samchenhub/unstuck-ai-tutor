"""Ranged MP4 downloader for Bilibili's CDN.

The CDN drops long single connections, and individual nodes behind a URL misbehave intermittently
(same-offset IncompleteRead, refused connections), so retrying one URL forever cannot succeed.
This downloader keeps partial bytes, resumes from the current offset, and fetches a fresh playback URL
(which usually lands on another node) after repeated failures.
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


class _Download:
    """State shared by the chunk workers: current URL (refreshed at most once per failure), total size, output fd."""

    def __init__(self, get_url, fd, headers, chunk_size, attempts_per_url, max_refreshes, opener, sleep):
        self.get_url, self.fd, self.headers, self.chunk_size = get_url, fd, headers, chunk_size
        self.attempts_per_url, self.max_refreshes, self.opener, self.sleep = attempts_per_url, max_refreshes, opener, sleep
        self.lock = threading.Lock()
        self.abort = threading.Event()
        self.refreshes = 0
        self.total: int | None = None
        self.url = get_url()

    def refresh(self, failed_url: str) -> str:
        """Replace the URL a worker failed on; if another worker already did, just adopt the new one."""
        with self.lock:
            if self.url == failed_url:
                self.refreshes += 1
                if self.refreshes > self.max_refreshes:
                    raise _fail()
                self.url = self.get_url()
            return self.url

    def chunk(self, start: int) -> int:
        """Fetch bytes [start, start + chunk_size) into the file, resuming after partial reads; returns end offset."""
        pos, failures = start, 0
        url = self.url
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
            if status == 403:
                url, failures = self.refresh(url), 0
                continue
            # A 200 means the server ignored Range and sent the whole file: only usable for the very first request.
            if status < 400 and body and not (status == 200 and pos):
                if status != 200:
                    body = body[:start + self.chunk_size - pos]
                os.pwrite(self.fd, body, pos)
                pos += len(body)
                failures = 0
                if size is not None:
                    self.total = size
                elif status == 200:
                    self.total = pos
                continue
            failures += 1
            log.warning("bilibili download failure %d at byte %d (status %s)", failures, pos, status)
            if failures >= self.attempts_per_url:
                url, failures = self.refresh(url), 0
            else:
                self.sleep(min(2 ** failures, 8))
        return pos


def download_ranges(
    get_url: Callable[[], str],
    dest: Path,
    *,
    headers: dict[str, str],
    chunk_size: int = 1 << 20,
    attempts_per_url: int = 3,
    max_refreshes: int = 4,
    workers: int = 4,
    opener: Opener | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with part.open("wb") as out:
            dl = _Download(get_url, out.fileno(), headers, chunk_size, attempts_per_url, max_refreshes,
                           opener or _open_range, sleep)
            written = dl.chunk(0)  # sequential: learns the total size from the first response
            total = dl.total
            if total is None:
                raise _fail()
            out.truncate(total)
            first = -(-written // chunk_size) * chunk_size
            starts = range(first, total, chunk_size)
            if starts:
                _fetch_parallel(dl, starts, workers)
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest


def _fetch_parallel(dl: _Download, starts: range, workers: int) -> None:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(dl.chunk, start) for start in starts]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            dl.abort.set()
            for future in futures:
                future.cancel()
            raise
