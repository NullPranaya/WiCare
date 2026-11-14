from pathlib import Path

import pytest

from fallguard.config import load_config


def test_defaults():
    c = load_config({}, Path("/x"))
    assert c.boards == (("b1", "http://172.20.10.3:62587/espectre/v1"),)
    assert c.rule.threshold == 0.962 and c.rule.burst_max_s == 3.0 and c.rule.still_s == 8.0
    assert c.cooldown_s == 60.0 and c.nurse_phone is None and c.data_dir == Path("/x/data")


def test_parses_two_boards_and_overrides():
    c = load_config(
        {
            "FALLGUARD_BOARDS": "a=http://1.2.3.4/espectre/v1, b=http://1.2.3.5:81/espectre/v1",
            "FALLGUARD_THRESHOLD": "0.9",
            "NURSE_PHONE": "+15550001111",
        },
        Path("/x"),
    )
    assert c.boards == (("a", "http://1.2.3.4/espectre/v1"), ("b", "http://1.2.3.5:81/espectre/v1"))
    assert c.rule.threshold == 0.9 and c.nurse_phone == "+15550001111"


def test_rejects_bad_board_entry():
    with pytest.raises(ValueError):
        load_config({"FALLGUARD_BOARDS": "no-equals-sign"}, Path("/x"))
