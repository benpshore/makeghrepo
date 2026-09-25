import random
import re

import pytest

from makeghrepo import names


def test_random_name_is_valid_adjective_noun():
    name = names.random_name(random.Random(0))
    adj, noun = name.split("-")
    assert adj in names.ADJECTIVES
    assert noun in names.NOUNS
    assert names.validate_name(name) == name


def test_word_lists_are_clean():
    for word in names.ADJECTIVES + names.NOUNS:
        assert re.fullmatch(r"[a-z]+", word), word
    assert len(set(names.ADJECTIVES)) == len(names.ADJECTIVES)
    assert len(set(names.NOUNS)) == len(names.NOUNS)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("My Cool_Repo!", "my-cool-repo"), ("  --x--  ", "x"), ("a.b", "a-b")],
)
def test_normalize_name(raw, expected):
    assert names.normalize_name(raw) == expected


@pytest.mark.parametrize("bad", ["", "-x", "x-", "UPPER", "a_b", "a" * 101, "../etc"])
def test_validate_name_rejects(bad):
    with pytest.raises(ValueError):
        names.validate_name(bad)


def test_package_name():
    assert names.package_name("quiet-otter") == "quiet_otter"
    assert names.package_name("3d-thing") == "_3d_thing"


def test_unique_random_name_skips_taken():
    taken = {names.random_name(random.Random(1))}
    got = names.unique_random_name(lambda n: n in taken, rng=random.Random(1))
    assert got not in taken


def test_unique_random_name_gives_up():
    with pytest.raises(RuntimeError):
        names.unique_random_name(lambda _: True, attempts=3)
