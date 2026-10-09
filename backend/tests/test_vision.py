from tests.factories import make_keyframe
from unstuck.ingest.vision import describe_pending, parse_vision_output
from unstuck.llm import FakeLLM


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
