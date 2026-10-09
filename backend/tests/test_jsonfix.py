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
