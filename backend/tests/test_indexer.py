from tests.factories import make_keyframe
from unstuck.db import repo
from unstuck.ingest.indexer import build_index, search, segment_text
from unstuck.models import Segment

VID = "abcdefghijk"


def test_segment_text_includes_only_ok_frames():
    segment = Segment(0, 30, "we talk", [1, 2])
    frames = {
        1: make_keyframe(1, 5.0, summary="Slide on vectors", ocr_text="v = (1, 2)"),
        2: make_keyframe(2, 10.0, status="failed", summary="ignored"),
    }
    text = segment_text(segment, frames)
    assert "we talk" in text
    assert "Slide on vectors" in text
    assert "v = (1, 2)" in text
    assert "ignored" not in text


def test_build_index_and_search(conn, fake_llm):
    repo.replace_segments(conn, VID, [
        Segment(0, 30, "introduction to the course"),
        Segment(30, 60, "eigenvalues and eigenvectors explained"),
        Segment(60, 90, "summary and goodbye"),
    ])
    assert build_index(conn, fake_llm, VID) == 3

    segments = repo.get_segments(conn, VID)
    assert search(conn, fake_llm, VID, "what are eigenvectors", k=1) == [segments[1].id]


def test_build_index_with_no_segments(conn, fake_llm):
    assert build_index(conn, fake_llm, VID) == 0
    assert search(conn, fake_llm, VID, "anything", k=3) == []


def test_build_index_rejects_mismatched_vector_count(conn):
    import pytest

    class ShortLLM:
        def embed(self, texts):
            return [[1.0] * 4 for _ in texts[:-1]]

    repo.replace_segments(conn, VID, [Segment(0, 30, "a"), Segment(30, 60, "b")])
    with pytest.raises(ValueError):
        build_index(conn, ShortLLM(), VID)
