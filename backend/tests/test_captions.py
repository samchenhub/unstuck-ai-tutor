import pytest

from unstuck.ingest.captions import parse_json3, sentence_end_flags
from unstuck.models import Cue


def test_parse_manual_json3():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Hello there."}]},
            {"tStartMs": 2500, "dDurationMs": 1500, "segs": [{"utf8": "Second\nline"}]},
        ]
    }
    assert parse_json3(data) == [Cue(0.0, 2.0, "Hello there."), Cue(2.5, 4.0, "Second line")]


def test_skips_events_without_text():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 100},
            {"tStartMs": 0, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 1000, "dDurationMs": 500, "segs": [{"utf8": "ok"}]},
        ]
    }
    assert parse_json3(data) == [Cue(1.0, 1.5, "ok")]


def test_auto_captions_estimate_end_from_word_offsets():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 5000,
             "segs": [{"utf8": "so"}, {"utf8": " today", "tOffsetMs": 400}, {"utf8": " we", "tOffsetMs": 900}]},
            {"tStartMs": 3000, "dDurationMs": 4000,
             "segs": [{"utf8": "learn"}, {"utf8": " vectors", "tOffsetMs": 500}]},
        ]
    }
    first, second = parse_json3(data)
    assert first.text == "so today we"
    assert first.start == 0.0 and first.end == pytest.approx(1.2)
    assert second.text == "learn vectors"
    assert second.start == 3.0 and second.end == pytest.approx(3.8)


def test_overlapping_cues_are_clipped_to_next_start():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 3000, "segs": [{"utf8": "one"}]},
            {"tStartMs": 2000, "dDurationMs": 1000, "segs": [{"utf8": "two"}]},
        ]
    }
    assert parse_json3(data) == [Cue(0.0, 2.0, "one"), Cue(2.0, 3.0, "two")]


def test_sentence_end_by_punctuation_and_pause():
    cues = [
        Cue(0, 1, "This is one."),
        Cue(1, 2, "and this"),      # 1.0 s pause follows
        Cue(3, 4, "continues"),     # 0.1 s gap follows
        Cue(4.1, 5, "所以是这样"),
        Cue(5, 6, "对吗？"),
        Cue(6, 7, "end"),           # last cue
    ]
    assert sentence_end_flags(cues, pause=0.8) == [True, True, False, False, True, True]
