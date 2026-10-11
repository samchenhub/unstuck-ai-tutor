"""Ranged MP4 downloader for Bilibili's CDN.

The CDN drops long single connections, and individual nodes behind a URL misbehave intermittently
(same-offset IncompleteRead, refused connections), so retrying one URL forever cannot succeed.
This downloader keeps partial bytes, resumes from the current offset, and fetches a fresh playback URL
(which usually lands on another node) after repeated failures.
"""

import http.client
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
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


def download_ranges(
    get_url: Callable[[], str],
    dest: Path,
    *,
    headers: dict[str, str],
    chunk_size: int = 1 << 20,
    attempts_per_url: int = 3,
    max_refreshes: int = 4,
    opener: Opener | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    opener = opener or _open_range
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        _fetch(get_url, part, headers, chunk_size, attempts_per_url, max_refreshes, opener, sleep)
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest


def _fetch(get_url, part, headers, chunk_size, attempts_per_url, max_refreshes, opener, sleep) -> None:
    url = get_url()
    refreshes = failures = offset = 0
    total: int | None = None
    with part.open("wb") as out:
        while total is None or offset < total:
            request_headers = {**headers, "Range": f"bytes={offset}-{offset + chunk_size - 1}"}
            expired = False
            try:
                status, body, size = opener(url, request_headers)
            except _FAILURES as exc:
                log.warning("bilibili download error at byte %d: %s", offset, exc)
                status, body, size = 0, b"", None
            if status == 403:
                expired = True
            elif status < 400 and body and not (status == 200 and offset):  # 200 mid-file: Range was ignored
                out.write(body)
                offset += len(body)
                failures = 0
                total = size if size is not None else (offset if status == 200 else total)
                continue
            else:
                failures += 1
                log.warning("bilibili download failure %d at byte %d (status %s)", failures, offset, status)
            if expired or failures >= attempts_per_url:
                refreshes += 1
                if refreshes > max_refreshes:
                    raise _fail()
                url = get_url()
                failures = 0
            else:
                sleep(min(2 ** failures, 8))
