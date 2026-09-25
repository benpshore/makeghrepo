"""Random adjective-noun repo names, e.g. ``quiet-otter``."""

from __future__ import annotations

import random
import re
from collections.abc import Callable

ADJECTIVES = tuple(
    """
    amber ancient autumn bold brave bright brisk calm clever cosmic crimson crisp
    curious dapper daring dusty eager electric fancy fluffy fuzzy gentle gilded glad
    golden grand happy hidden humble icy jolly keen kind lively lucky lunar mellow
    misty modest mossy nimble noble odd polite proud quick quiet rapid rustic shiny
    silent silver sleepy sly smooth snowy solar sunny swift tidy tiny vivid wandering
    warm witty zesty
    """.split()
)

NOUNS = tuple(
    """
    acorn badger beacon bison brook cactus canyon comet coral crane dune ember falcon
    fern finch fjord fox garden geyser glacier harbor heron hollow island lagoon
    lantern lichen lynx maple meadow meteor moose nebula newt oasis orbit otter owl
    panda pebble pine prairie quartz rabbit raven reef river sparrow spruce summit
    system thicket tiger tundra valley walrus willow wombat yak zephyr
    """.split()
)

# GitHub allows [A-Za-z0-9._-]; we are stricter so the name is also a sane
# directory and a valid Python distribution name.
_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$|^[a-z0-9]$")


def random_name(rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    return f"{rng.choice(ADJECTIVES)}-{rng.choice(NOUNS)}"


def normalize_name(name: str) -> str:
    """Lowercase and collapse anything that isn't [a-z0-9] into single dashes."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def validate_name(name: str) -> str:
    if not _VALID_NAME.match(name):
        raise ValueError(
            f"invalid repo name {name!r}: use lowercase letters, digits and "
            "dashes (not leading/trailing), max 100 chars"
        )
    return name


def package_name(name: str) -> str:
    """Python import name for a repo name: ``quiet-otter`` -> ``quiet_otter``."""
    pkg = name.replace("-", "_")
    return f"_{pkg}" if pkg[0].isdigit() else pkg


def unique_random_name(
    is_taken: Callable[[str], bool], attempts: int = 25, rng: random.Random | None = None
) -> str:
    rng = rng or random.Random()
    for _ in range(attempts):
        candidate = random_name(rng)
        if not is_taken(candidate):
            return candidate
    raise RuntimeError(f"could not find a free random name in {attempts} attempts")
