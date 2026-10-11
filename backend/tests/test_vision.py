import pytest

from tests.factories import make_keyframe
from unstuck.ingest.vision import describe_frame, parse_vision_output
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


def test_describe_frame_passes_the_previous_summary_and_one_model_attempt():
    llm = FakeLLM(vision_responses=[
        '{"kind": "formula", "summary": "Pythagoras", "ocr_text": "a^2+b^2=c^2", "change_note": "formula appears"}',
    ])
    desc = describe_frame(llm, make_keyframe(2, 20.0, status="pending"), "Title slide")
    assert desc.kind == "formula" and desc.summary == "Pythagoras"
    (prompt, _path, attempts), = [args for name, args in llm.calls if name == "vision"]
    assert "Title slide" in prompt
    assert attempts == 1  # callers decide how to wait; the client must not retry a rate limit for a minute


def test_describe_frame_raises_instead_of_marking_failed():
    with pytest.raises(ValueError):
        describe_frame(FakeLLM(vision_responses=["  "]), make_keyframe(1, 1.0), "")
    with pytest.raises(ModelBusyError):
        describe_frame(FakeLLM(vision_responses=[ModelBusyError("429")]), make_keyframe(1, 1.0), "")


def test_null_fields_become_empty_strings():
    d = parse_vision_output('{"kind": "slide", "summary": "Agenda", "ocr_text": null, "change_note": null}')
    assert d.ocr_text == ""
    assert d.change_note == ""


def test_ocr_text_keeps_leading_indentation():
    d = parse_vision_output('{"kind": "code", "summary": "s", "ocr_text": "    return x\\n", "change_note": ""}')
    assert d.ocr_text == "    return x"
