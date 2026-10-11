from PIL import Image

from unstuck.ingest import keyframes
from unstuck.ingest.keyframes import dedupe_by_phash, extract_keyframes, sample_times
from unstuck.models import NewKeyframe


def kf(t, phash):
    return NewKeyframe(t=t, image_path=f"/f/{t}.jpg", phash=phash)


def test_sample_times_are_even_and_capped():
    assert sample_times(180.0) == [22.5, 67.5, 112.5, 157.5]  # one frame per 45 s, centred
    assert sample_times(3.0) == [1.5]  # a short clip still gets its middle frame
    long = sample_times(4 * 3600.0)
    assert len(long) == 200 and long[1] - long[0] == 72.0  # long videos: the interval grows instead


def test_dedupe_by_phash_compares_with_last_kept():
    frames = [
        kf(1, "0000000000000000"),
        kf(2, "0000000000000001"),  # distance 1 → duplicate of frame 1
        kf(3, "ffffffffffffffff"),  # distance 64 → kept
        kf(4, "fffffffffffffff0"),  # distance 4 from frame 3 → duplicate
    ]
    assert [f.t for f in dedupe_by_phash(frames, threshold=5)] == [1, 3]


def test_extract_keyframes_on_real_video(sample_video, tmp_path):
    frames = extract_keyframes(sample_video, duration=3.0, out_dir=tmp_path / "frames")
    assert len(frames) == 1
    assert (tmp_path / "frames").exists()
    assert len(frames[0].phash) == 16


def patterned(t: float) -> Image.Image:
    """Distinct pictures per t (solid colours all share one perceptual hash)."""
    import random

    rng = random.Random(int(t))
    img = Image.new("L", (8, 8))
    img.putdata([rng.choice((0, 255)) for _ in range(64)])
    return img.resize((64, 64)).convert("RGB")


def fake_frames(monkeypatch, calls, bad_until=0.0, colors=None):
    def fake_extract(video, t, out, headers=None):
        calls.append((video, t, headers))
        out.parent.mkdir(parents=True, exist_ok=True)
        if t < bad_until:
            out.write_bytes(b"not a jpeg")
        else:
            color = (colors or {}).get(t, (int(t) % 256, 0, 255 - int(t) % 256))
            Image.new("RGB", (64, 64), color).save(out, "JPEG")
        return out

    monkeypatch.setattr(keyframes.media, "extract_frame", fake_extract)


def test_unreadable_frame_is_skipped_not_fatal(tmp_path, monkeypatch):
    calls = []
    fake_frames(monkeypatch, calls, bad_until=30.0, colors={67.5: "white"})
    frames = extract_keyframes(tmp_path / "v.mp4", duration=90.0, out_dir=tmp_path / "frames")
    assert [f.t for f in frames] == [67.5]


def test_frames_from_a_stream_send_headers_and_refresh_an_expired_url(tmp_path, monkeypatch):
    calls = []
    urls = iter(["https://cdn/old.mp4", "https://cdn/new.mp4"])

    def fake_extract(video, t, out, headers=None):
        calls.append((video, t, headers))
        if video.endswith("old.mp4") and t > 50:
            raise RuntimeError("403 Forbidden")
        out.parent.mkdir(parents=True, exist_ok=True)
        patterned(t).save(out, "JPEG")
        return out

    monkeypatch.setattr(keyframes.media, "extract_frame", fake_extract)
    frames = extract_keyframes(lambda: next(urls), duration=450.0, out_dir=tmp_path / "frames",
                               headers={"Referer": "https://www.bilibili.com/"})
    assert all(h == {"Referer": "https://www.bilibili.com/"} for _v, _t, h in calls)
    assert calls[-1][0] == "https://cdn/new.mp4"  # refreshed after repeated failures
    assert len(frames) >= 5


def test_no_frame_at_all_is_a_failure(tmp_path, monkeypatch):
    import pytest

    from unstuck.errors import IngestError

    def broken(video, t, out, headers=None):
        raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(keyframes.media, "extract_frame", broken)
    with pytest.raises(IngestError) as err:
        extract_keyframes(lambda: "https://cdn/v.mp4", duration=200.0, out_dir=tmp_path / "frames")
    assert err.value.code == "KEYFRAMES_FAILED"
