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


def test_publish_index_reuses_embeddings_and_keeps_the_old_index_on_failure(conn, fake_llm):
    from unstuck.ingest.indexer import publish_index

    embedded = []
    real_embed = fake_llm.embed

    def counting_embed(texts):
        embedded.extend(texts)
        return real_embed(texts)

    fake_llm.embed = counting_embed
    cache: dict[str, list[float]] = {}
    first = [Segment(0, 30, "introduction to the course"), Segment(30, 60, "eigenvalues explained")]
    publish_index(conn, fake_llm, VID, first, cache)
    assert embedded == ["introduction to the course", "eigenvalues explained"]

    publish_index(conn, fake_llm, VID, [*first, Segment(60, 90, "summary and goodbye")], cache)
    assert embedded[2:] == ["summary and goodbye"]  # unchanged segments are not embedded again
    assert [s.transcript for s in repo.get_segments(conn, VID)][-1] == "summary and goodbye"
    assert len(search(conn, fake_llm, VID, "goodbye", k=5)) == 3

    def broken(texts):
        raise RuntimeError("embedder down")

    fake_llm.embed = broken
    try:
        publish_index(conn, fake_llm, VID, [Segment(0, 40, "something new")], {})
    except RuntimeError:
        pass
    fake_llm.embed = real_embed
    assert len(repo.get_segments(conn, VID)) == 3  # nothing was replaced before the embeddings existed
    assert len(search(conn, fake_llm, VID, "goodbye", k=5)) == 3
