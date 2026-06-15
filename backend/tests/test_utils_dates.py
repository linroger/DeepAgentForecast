"""Golden tests for lenient date parsing (EXECPLAN2 I-7-3)."""

from datetime import datetime, timezone

from app.utils.dates import parse_as_of


def test_parse_iso_and_slash_and_dot():
    for s in ("2030-06-15", "2030/06/15", "2030.06.15"):
        d = parse_as_of(s)
        assert d == datetime(2030, 6, 15, tzinfo=timezone.utc)


def test_parse_cjk():
    assert parse_as_of("2030年6月15日") == datetime(2030, 6, 15, tzinfo=timezone.utc)
    assert parse_as_of("2030年6月") == datetime(2030, 6, 1, tzinfo=timezone.utc)


def test_parse_year_only_and_year_month():
    assert parse_as_of("2030") == datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert parse_as_of("2030-06") == datetime(2030, 6, 1, tzinfo=timezone.utc)


def test_parse_passthrough_and_failures():
    assert parse_as_of(None) is None
    assert parse_as_of("") is None
    assert parse_as_of("no date here") is None
    naive = datetime(2030, 1, 2)
    assert parse_as_of(naive).tzinfo is not None  # naive -> UTC-stamped


def test_parse_overflow_day_falls_back_to_first():
    # 2 月 30 日 -> 当月 1 日
    assert parse_as_of("2030-02-30") == datetime(2030, 2, 1, tzinfo=timezone.utc)
