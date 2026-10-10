from pathlib import Path

import pytest

from unstuck.media import extract_frame


def test_extract_frame(sample_video, tmp_path):
    out = extract_frame(sample_video, 1.5, tmp_path / "sub" / "f.jpg")
    assert out.exists() and out.stat().st_size > 0


def test_extract_frame_past_end_raises(sample_video, tmp_path):
    with pytest.raises(RuntimeError):
        extract_frame(sample_video, 10.0, tmp_path / "late.jpg")


def _capture_argv(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["argv"] = cmd
        Path(cmd[-1]).write_bytes(b"jpg")

    monkeypatch.setattr("unstuck.media.subprocess.run", fake_run)
    return captured


def test_extract_frame_sends_headers_before_input(tmp_path, monkeypatch):
    captured = _capture_argv(monkeypatch)
    headers = {"User-Agent": "UA/1.0", "Referer": "https://www.bilibili.com/"}
    extract_frame("https://cdn.example/v.mp4", 3.0, tmp_path / "f.jpg", headers=headers)
    argv = captured["argv"]
    i = argv.index("-i")
    assert argv[argv.index("-user_agent") + 1] == "UA/1.0"
    assert argv[argv.index("-headers") + 1] == "Referer: https://www.bilibili.com/\r\n"
    assert argv.index("-user_agent") < i and argv.index("-headers") < i


def test_extract_frame_without_headers_adds_no_header_flags(tmp_path, monkeypatch):
    captured = _capture_argv(monkeypatch)
    extract_frame("https://cdn.example/v.mp4", 3.0, tmp_path / "f.jpg")
    assert "-user_agent" not in captured["argv"] and "-headers" not in captured["argv"]
