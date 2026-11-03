from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import app

LONDON = ZoneInfo("Europe/London")


def at(hour, minute, day=3):
    return datetime(2026, 11, day, hour, minute, tzinfo=LONDON)


def test_single_time_still_works():
    assert app.parse_daily_times("05:30") == [(5, 30)]


def test_several_times_are_sorted_and_deduplicated():
    assert app.parse_daily_times(" 17:30, 05:30 ,05:30") == [(5, 30), (17, 30)]


@pytest.mark.parametrize("value", ["", " , ", "5", "25:00", "05:60", "05:30,later"])
def test_bad_values_are_rejected(value):
    with pytest.raises(ValueError):
        app.parse_daily_times(value)


@pytest.mark.parametrize("now, expected", [
    (at(4, 0), at(5, 30)),
    (at(5, 30), at(17, 30)),
    (at(12, 0), at(17, 30)),
    (at(18, 0), at(5, 30, day=4)),
])
def test_next_run_is_the_nearest_upcoming_time(now, expected):
    assert app.next_daily_run([(5, 30), (17, 30)], now) == expected
