import pytest

from unstuck.timefmt import fmt_ts, parse_ts


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "00:00"), (59.9, "00:59"), (215, "03:35"), (3725, "1:02:05"), (-3, "00:00")],
)
def test_fmt_ts(seconds, expected):
    assert fmt_ts(seconds) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("03:35", 215.0), ("1:02:05", 3725.0), ("0:07", 7.0), (" 12:00 ", 720.0)],
)
def test_parse_ts(text, expected):
    assert parse_ts(text) == expected


@pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "03:75", "1:60:00"])
def test_parse_ts_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_ts(bad)
