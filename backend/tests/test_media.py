import pytest

from unstuck.media import extract_frame


def test_extract_frame(sample_video, tmp_path):
    out = extract_frame(sample_video, 1.5, tmp_path / "sub" / "f.jpg")
    assert out.exists() and out.stat().st_size > 0


def test_extract_frame_past_end_raises(sample_video, tmp_path):
    with pytest.raises(RuntimeError):
        extract_frame(sample_video, 10.0, tmp_path / "late.jpg")
