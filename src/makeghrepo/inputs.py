"""Positional grammar backed by the installed, declarative keyword registry.

Only a non-reserved first word can be a repository name. All later words must
dispatch to an explicit operation. Registry values are data, never Python names
to import or evaluate; flags are separately parsed by Typer's fixed option schema.
"""

from __future__ import annotations

from types import MappingProxyType

from makeghrepo import registry

LANGS = registry.load()

# Reuse the TOML registry shared with the renderer. A second JSON copy would
# create another source of truth without changing the dispatch or trust boundary.
RESERVED_WORDS = MappingProxyType(
    {word: ("language", lang.id) for lang in LANGS.values() for word in (lang.id, *lang.aliases)}
)


def parse_words(words: list[str]) -> tuple[str | None, list[str]]:
    """Return the optional name and approved language actions, preserving order.

    A reserved first word configures its declared action and leaves the name
    unset. A non-reserved first word is only a name; it is validated separately,
    never retried as code or another command. Unknown later words are errors.
    Repeated aliases collapse to one action without changing the user's order.
    """
    name = None
    languages: list[str] = []
    for index, word in enumerate(words):
        match RESERVED_WORDS.get(word.lower()):
            case ("language", str(language)):
                if language not in languages:
                    languages.append(language)
            case None if index == 0:
                name = word
            case None:
                raise ValueError(
                    f"unknown language/tool word {word!r}; only the first word can name a repo"
                )
            case _:
                raise ValueError("unsupported action in the installed keyword registry")
    return name, languages
