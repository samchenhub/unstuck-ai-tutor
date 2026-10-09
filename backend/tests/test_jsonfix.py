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


def test_failed_outer_object_is_repaired_not_replaced_by_inner_list():
    raw = r'{"kind":"formula","summary":"s","ocr_text":"\alpha + x[0]","change_note":""}'
    obj = loads_lenient(raw)
    assert isinstance(obj, dict)
    assert obj["kind"] == "formula"
    assert "x[0]" in obj["ocr_text"]


def test_literal_newline_in_string_keeps_outer_object():
    raw = '{"kind":"code","summary":"s","ocr_text":"a = 1\nb = arr[0]","change_note":""}'
    obj = loads_lenient(raw)
    assert isinstance(obj, dict)
    assert "arr[0]" in obj["ocr_text"]


def test_truncated_object_with_inner_list_keeps_outer_object():
    raw = '{"kind":"code","summary":"s","ocr_text":"x = [1, 2]", "change_no'
    obj = loads_lenient(raw)
    assert isinstance(obj, dict)
    assert obj["ocr_text"] == "x = [1, 2]"


def test_truncated_list_of_objects_keeps_the_list():
    obj = loads_lenient('[{"a": 1}, {"b": ')
    assert isinstance(obj, list)
    assert obj[0] == {"a": 1}


def test_already_escaped_backslash_is_untouched():
    assert loads_lenient(r'{"o": "\\theta"}') == {"o": "\\theta"}
