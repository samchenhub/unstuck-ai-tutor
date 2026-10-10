import pytest

from tests.factories import make_keyframe
from unstuck.ingest.vision import describe_pending, parse_vision_output
from unstuck.llm import FakeLLM, ModelBusyError


def test_parse_clean_json():
    d = parse_vision_output('{"kind": "code", "summary": "A loop", "ocr_text": "for i in x:", "change_note": "new"}')
    assert (d.kind, d.summary, d.ocr_text, d.change_note) == ("code", "A loop", "for i in x:", "new")


def test_parse_fenced_and_chatty():
    d = parse_vision_output('Sure!\n```json\n{"kind": "slide", "summary": "Agenda"}\n```')
    assert d.kind == "slide" and d.summary == "Agenda" and d.ocr_text == ""


def test_parse_truncated():
    d = parse_vision_output('{"kind": "formula", "summary": "Pythagoras", "ocr_text": "a^2 + b^2 = c^')
    assert d.kind == "formula"
    assert d.summary == "Pythagoras"
    assert d.ocr_text.startswith("a^2 + b^2")


def test_unknown_kind_becomes_other():
    assert parse_vision_output('{"kind": "meme", "summary": "x"}').kind == "other"


def test_non_json_output_becomes_summary():
    d = parse_vision_output("The slide shows a matrix multiplication example.")
    assert d.kind == "other"
    assert d.summary == "The slide shows a matrix multiplication example."


def test_describe_pending_skips_ok_frames_and_chains_previous_summary():
    frames = [
        make_keyframe(1, 1.0, status="ok", summary="Title slide"),
        make_keyframe(2, 20.0, status="pending"),
        make_keyframe(3, 40.0, status="failed"),
    ]
    llm = FakeLLM(vision_responses=[
        '{"kind": "formula", "summary": "Pythagoras", "ocr_text": "a^2+b^2=c^2", "change_note": "formula appears"}',
        '{"kind": "code", "summary": "Python loop", "ocr_text": "for i in range(3):", "change_note": ""}',
    ])
    saved = {}

    failed = describe_pending(llm, frames, saved.__setitem__, sleep=lambda s: None)

    assert failed == 0
    assert set(saved) == {2, 3}
    assert saved[2].kind == "formula"
    prompts = [args[0] for name, args in llm.calls if name == "vision"]
    assert "Title slide" in prompts[0]
    assert "Pythagoras" in prompts[1]


def test_one_failing_frame_does_not_stop_the_rest():
    frames = [make_keyframe(1, 1.0, status="pending"), make_keyframe(2, 20.0, status="pending")]
    llm = FakeLLM(vision_responses=[
        RuntimeError("429"), RuntimeError("429"),
        '{"kind": "slide", "summary": "Agenda", "ocr_text": "", "change_note": ""}',
    ])
    saved = {}

    failed = describe_pending(llm, frames, saved.__setitem__, attempts=2, sleep=lambda s: None)

    assert failed == 1
    assert saved[1] is None
    assert saved[2].summary == "Agenda"


def test_null_fields_become_empty_strings():
    d = parse_vision_output('{"kind": "slide", "summary": "Agenda", "ocr_text": null, "change_note": null}')
    assert d.ocr_text == ""
    assert d.change_note == ""


def test_ocr_text_keeps_leading_indentation():
    d = parse_vision_output('{"kind": "code", "summary": "s", "ocr_text": "    return x\\n", "change_note": ""}')
    assert d.ocr_text == "    return x"


def test_blank_reply_is_a_failure_after_retries():
    frames = [make_keyframe(1, 1.0, status="pending")]
    llm = FakeLLM(vision_responses=["", "  "])
    saved = {}

    failed = describe_pending(llm, frames, saved.__setitem__, attempts=2, sleep=lambda s: None)

    assert failed == 1
    assert saved[1] is None


def test_model_busy_stops_the_loop_and_leaves_remaining_frames_pending():
    frames = [make_keyframe(i, float(i), status="pending") for i in (1, 2, 3)]
    ok = '{"kind": "slide", "summary": "Agenda", "ocr_text": "", "change_note": ""}'
    llm = FakeLLM(vision_responses=[ok, ModelBusyError("x")])
    saved = {}

    with pytest.raises(ModelBusyError):
        describe_pending(llm, frames, saved.__setitem__, attempts=3, sleep=lambda s: None)

    assert set(saved) == {1}
    assert len([c for c in llm.calls if c[0] == "vision"]) == 2  # busy error is not retried

    later = FakeLLM(vision_responses=[ok, ok])
    done = [make_keyframe(1, 1.0, status="ok", summary="Agenda")] + frames[1:]
    saved2 = {}
    assert describe_pending(later, done, saved2.__setitem__, sleep=lambda s: None) == 0
    assert set(saved2) == {2, 3}
