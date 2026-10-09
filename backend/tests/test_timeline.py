from tests.factories import make_keyframe
from unstuck.ingest.timeline import build_segments
from unstuck.models import Cue


def cues_every(n, length=5.0, texts=None):
    texts = texts or {}
    return [Cue(i * length, (i + 1) * length, texts.get(i, f"w{i}")) for i in range(n)]


def spans(segments):
    return [(s.start, s.end) for s in segments]


def test_cuts_at_first_sentence_end_after_30s():
    cues = cues_every(20, texts={3: "too early.", 8: "first.", 15: "second."})
    assert spans(build_segments(cues, [], 100)) == [(0, 45), (45, 80), (80, 100)]


def test_forces_cut_at_60s_without_sentence_ends():
    assert spans(build_segments(cues_every(30), [], 150)) == [(0, 60), (60, 120), (120, 150)]


def test_scene_change_cuts_after_45s_and_assigns_keyframes():
    frames = [make_keyframe(1, 36.0), make_keyframe(2, 51.0)]
    segments = build_segments(cues_every(20), frames, 100)
    assert spans(segments) == [(0, 50), (50, 100)]
    assert segments[0].keyframe_ids == [1]
    assert segments[1].keyframe_ids == [2]


def test_short_tail_merges_into_previous():
    cues = cues_every(13, texts={11: "done."})
    assert spans(build_segments(cues, [], 65)) == [(0, 65)]


def test_transcript_joins_cue_text():
    cues = cues_every(3, texts={0: "Hello", 1: "vector", 2: "world."})
    (segment,) = build_segments(cues, [], 15)
    assert segment.transcript == "Hello vector world."


def test_keyframe_before_first_cue_goes_to_first_segment():
    cues = [Cue(5.0, 10.0, "late start.")]
    (segment,) = build_segments(cues, [make_keyframe(7, 1.0)], 10)
    assert segment.keyframe_ids == [7]


def test_no_cues_makes_fixed_windows():
    segments = build_segments([], [make_keyframe(3, 50.0)], 100)
    assert spans(segments) == [(0, 45), (45, 90), (90, 100)]
    assert all(s.transcript == "" for s in segments)
    assert segments[1].keyframe_ids == [3]
