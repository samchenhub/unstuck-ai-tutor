from unstuck.jsonfix import loads_lenient


def test_plain_json():
    assert loads_lenient('{"a": 1}') == {"a": 1}


def test_fenced_json():
    assert loads_lenient('```json\n{"a": 1}\n```') == {"a": 1}


def test_surrounding_text():
    assert loads_lenient('Here you go: {"a": 1} hope it helps') == {"a": 1}


def test_truncated_json_is_repaired():
    assert loads_lenient('{"a": 1, "b": "hel') == {"a": 1, "b": "hel"}


def test_list():
    assert loads_lenient("[1, 2]") == [1, 2]


def test_no_json():
    assert loads_lenient("no json here") is None


def test_unfenced_object_containing_a_fenced_snippet_is_not_truncated():
    raw = '{"kind": "code", "ocr_text": "```python\\nprint(1)\\n```"}'
    obj = loads_lenient(raw)
    assert isinstance(obj, dict)
    assert "print(1)" in obj["ocr_text"]


def test_bracket_reference_before_object():
    assert loads_lenient('See [1] for ref. {"a": 1}') == {"a": 1}


def test_latex_backslashes_survive():
    raw = r'{"o": "\theta + \frac{1}{2}"}'
    assert loads_lenient(raw) == {"o": "\\theta + \\frac{1}{2}"}


def test_ordinary_json_escapes_still_decode():
    assert loads_lenient(r'{"o": "a\nfor b"}') == {"o": "a\nfor b"}
