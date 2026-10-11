import math
import threading
import time

import pytest

from unstuck.errors import IngestError
from unstuck.sources.base import AudioFeed


def test_complete_feed_is_ready_for_anything(tmp_path):
    feed = AudioFeed.complete(tmp_path / "a.m4a")
    assert feed.is_complete and feed.readable_seconds() == math.inf
    feed.wait_for(1e9)  # returns at once
    assert feed.path() == tmp_path / "a.m4a"


def test_readable_seconds_follow_the_downloaded_prefix(tmp_path):
    feed = AudioFeed(tmp_path / "a.m4a", duration=1000.0)
    assert feed.readable_seconds() == 0
    feed.progress(256 * 1024 + 500_000, 1_000_000)  # half of the file past the safety margin
    assert feed.readable_seconds() == pytest.approx(500 * 0.97)
    feed.progress(256 * 1024, 1_000_000)  # reports can arrive out of order: keep the max
    assert feed.readable_seconds() == pytest.approx(500 * 0.97)


def test_path_is_the_part_file_until_the_download_is_renamed(tmp_path):
    dest = tmp_path / "audio.m4a"
    feed = AudioFeed(dest, duration=10.0)
    assert feed.path() == tmp_path / "audio.m4a.part"
    dest.write_bytes(b"x")
    assert feed.path() == dest
    feed.finish(tmp_path / "video.mp4")  # e.g. fell back to the MP4
    assert feed.path() == tmp_path / "video.mp4"


def test_wait_for_blocks_until_enough_is_downloaded_or_it_finishes(tmp_path):
    feed = AudioFeed(tmp_path / "a.m4a", duration=100.0)

    def producer():
        for done in (300_000, 600_000, 900_000):
            time.sleep(0.01)
            feed.progress(done, 1_000_000)
        time.sleep(0.01)
        feed.finish()

    threading.Thread(target=producer).start()
    feed.wait_for(50.0)
    assert feed.readable_seconds() >= 50.0
    feed.wait_for(1e9)
    assert feed.is_complete


def test_wait_raises_the_download_error(tmp_path):
    feed = AudioFeed(tmp_path / "a.m4a", duration=100.0)
    threading.Timer(0.01, feed.fail, args=(IngestError("DOWNLOAD_FAILED", "视频下载失败"),)).start()
    with pytest.raises(IngestError):
        feed.wait_for(10.0)


def test_wait_for_more_returns_when_the_prefix_grows(tmp_path):
    feed = AudioFeed(tmp_path / "a.m4a", duration=100.0)
    feed.progress(300_000, 1_000_000)
    threading.Timer(0.01, feed.progress, args=(400_000, 1_000_000)).start()
    feed.wait_for_more()
    assert feed.readable_seconds() > 10


def test_reset_forgets_progress_until_the_fallback_finishes(tmp_path):
    feed = AudioFeed(tmp_path / "a.m4a", duration=100.0)
    feed.progress(900_000, 1_000_000)
    feed.reset()
    assert feed.readable_seconds() == 0 and not feed.is_complete
