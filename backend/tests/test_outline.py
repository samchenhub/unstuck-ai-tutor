from unstuck.ingest.outline import current_chapter, generate_outline, parse_outline
from unstuck.llm import FakeLLM
from unstuck.models import Segment


def test_parse_outline_normalizes_entries():
    raw = """```json
{"chapters": [
  {"start": "02:00", "title": "Matrices", "summary": "m"},
  {"start": 5, "title": "Vectors", "summary": "v"},
  {"start": 999, "title": "Beyond the end", "summary": ""},
  {"start": 60, "summary": "no title"}
]}
```"""
    outline = parse_outline(raw, duration=300)
    assert [c["title"] for c in outline] == ["Vectors", "Matrices"]
    assert outline[0]["start"] == 0.0
    assert outline[1] == {"start": 120.0, "title": "Matrices", "summary": "m"}


def test_parse_outline_accepts_bare_list():
    outline = parse_outline('[{"start": 0, "title": "Only"}]', duration=60)
    assert outline == [{"start": 0.0, "title": "Only", "summary": ""}]


def test_parse_outline_garbage():
    assert parse_outline("sorry, I cannot", duration=60) == []


def test_current_chapter():
    outline = [{"start": 0.0}, {"start": 120.0}, {"start": 200.0}]
    assert current_chapter(outline, 0) == 0
    assert current_chapter(outline, 119.9) == 0
    assert current_chapter(outline, 120) == 1
    assert current_chapter(outline, 250) == 2
    assert current_chapter([], 10) is None
    assert current_chapter(None, 10) is None


def test_generate_outline_sends_timestamped_transcript():
    llm = FakeLLM(chat_responses=['{"chapters": [{"start": 0, "title": "Intro", "summary": "s"}]}'])
    segments = [Segment(0, 30, "hello there"), Segment(30, 60, "dot products")]

    outline = generate_outline(llm, segments, title="Linear Algebra", duration=60)

    assert outline == [{"start": 0.0, "title": "Intro", "summary": "s"}]
    (_name, messages), = llm.calls
    prompt = messages[-1]["content"]
    assert "Linear Algebra" in prompt
    assert "[00:30] dot products" in prompt


def test_parse_outline_rejects_non_finite_starts():
    raw = '{"chapters": [{"start": 0, "title": "A"}, {"start": NaN, "title": "B"}, {"start": 30, "title": "C"}]}'
    outline = parse_outline(raw, 100)
    assert [c["title"] for c in outline] == ["A", "C"]


def test_parse_outline_rejects_boolean_starts():
    raw = '{"chapters": [{"start": true, "title": "X"}, {"start": 0, "title": "Y"}]}'
    outline = parse_outline(raw, 60)
    assert [c["title"] for c in outline] == ["Y"]


def test_parse_outline_skips_null_title_and_blanks_null_summary():
    raw = '[{"start": 0, "title": null}, {"start": 10, "title": "Real", "summary": null}]'
    assert parse_outline(raw, 60) == [{"start": 0.0, "title": "Real", "summary": ""}]


def test_generate_outline_skips_llm_when_no_segments():
    llm = FakeLLM(chat_responses=['{"chapters": [{"start": 0, "title": "Intro"}]}'])
    assert generate_outline(llm, [], "T", 60) == []
    assert llm.calls == []
