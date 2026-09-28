"""The language registry: one TOML file per language/component in ``langs/``.

Each file owns everything makeghrepo knows about one token (aliases, local checks,
CI job, Dependabot ecosystem, CodeQL language, npm scripts...), so adding a language
adds files instead of editing shared tables and templates. The Rust port reads the
same files. Field meanings (schema 1):

- ``id``/``order``: the token and its position in ``LANGUAGES`` (and so in every
  generated file that lists languages).
- ``aliases``: other words accepted on the command line.
- ``kind``: ``language`` (a toolchain) or ``component`` (a service or tool).
- ``flag``: its boolean in the template's ``copier.yml``.
- ``groups``: shared build setups it belongs to (``npm``, ``cmake``).
- ``marker``: a file that only this token renders (used by tests).
- ``checks``/``setup_len``/``required_tool``: local smoke-test commands, how many
  leading ones must run first and in order, and whether the first one's tool is
  mandatory (it creates a lockfile CI depends on).
- ``ci_job``: its job in the generated ci.yml (shared by a group, e.g. ``cmake``).
- ``dependabot``/``codeql``: ``{ecosystem, order}`` / ``{language, build_mode, os,
  order}``, or ``{}``; ``order`` fixes their position in the generated files.
- ``docker_priority``: which runtime the Dockerfile uses when several are chosen.
- ``npm_scripts``/``npm_dev``: what it adds to a shared package.json.
- ``requires``/``host_os``/``apps``/``lockfile``/``release``: reserved for later
  issues (capability probes, app kinds, release artifacts).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from importlib.resources import files
from pathlib import Path
from typing import Any

SCHEMA = 1


@dataclass(frozen=True)
class Lang:
    id: str
    order: int
    aliases: tuple[str, ...]
    kind: str
    flag: str
    groups: tuple[str, ...]
    marker: str
    checks: tuple[tuple[str, ...], ...]
    setup_len: int
    required_tool: bool
    ci_job: str
    dependabot: dict[str, Any]
    codeql: dict[str, Any]
    docker_priority: int
    requires: tuple[str, ...]
    host_os: tuple[str, ...]
    apps: tuple[str, ...]
    lockfile: str
    release: str
    npm_scripts: dict[str, str]
    npm_dev: dict[str, str]


_FIELDS = {f.name for f in fields(Lang)} | {"schema"}


def _parse(name: str, text: str) -> Lang:
    data = tomllib.loads(text)
    missing = _FIELDS - data.keys()
    extra = data.keys() - _FIELDS
    if missing or extra:
        raise ValueError(f"{name}: missing {sorted(missing)}, unknown {sorted(extra)}")
    if data["schema"] != SCHEMA:
        raise ValueError(f"{name}: schema {data['schema']!r}, expected {SCHEMA}")
    if f"{data['id']}.toml" != name:
        raise ValueError(f"{name}: id {data['id']!r} doesn't match the file name")
    if not 0 <= data["setup_len"] <= len(data["checks"]):
        raise ValueError(f"{name}: setup_len {data['setup_len']} out of range")
    if data["required_tool"] and not data["checks"]:
        raise ValueError(f"{name}: required_tool without any checks")
    del data["schema"]
    for key in ("aliases", "groups", "requires", "host_os", "apps"):
        data[key] = tuple(data[key])
    data["checks"] = tuple(tuple(cmd) for cmd in data["checks"])
    return Lang(**data)


def load(directory: Path | None = None) -> dict[str, Lang]:
    """All registry entries, ordered by ``order``. Defaults to the packaged ``langs/``."""
    source = directory if directory is not None else files("makeghrepo") / "langs"
    entries = [
        _parse(entry.name, entry.read_text(encoding="utf-8"))
        for entry in source.iterdir()
        if entry.name.endswith(".toml")
    ]
    entries.sort(key=lambda lang: lang.order)
    langs: dict[str, Lang] = {}
    words: set[str] = set()
    for lang in entries:
        for word in (lang.id, *lang.aliases):
            if word in words:
                raise ValueError(f"{lang.id}.toml: {word!r} is already an id or alias")
            words.add(word)
        langs[lang.id] = lang
    if len({lang.order for lang in entries}) != len(entries):
        raise ValueError("two registry entries share an order")
    return langs


def derived(langs: dict[str, Lang], languages: list[str]) -> dict[str, Any]:
    """Template data for the chosen languages, in registry order, with shared entries once."""
    wanted = set(languages)
    ci_jobs: list[str] = []
    ecosystems: dict[str, int] = {}
    codeql: dict[str, dict[str, Any]] = {}
    npm_scripts: dict[str, str] = {}
    npm_dev: dict[str, str] = {}
    for lang in langs.values():
        if lang.id not in wanted:
            continue
        if lang.ci_job and lang.ci_job not in ci_jobs:
            ci_jobs.append(lang.ci_job)
        if lang.dependabot:
            ecosystems[lang.dependabot["ecosystem"]] = lang.dependabot["order"]
        if lang.codeql:
            codeql[lang.codeql["language"]] = lang.codeql
        npm_scripts.update(lang.npm_scripts)
        npm_dev.update(lang.npm_dev)
    return {
        "ci_jobs": ci_jobs,
        "dependabot_ecosystems": ["github-actions", *sorted(ecosystems, key=ecosystems.__getitem__)],
        "codeql_entries": [
            {key: entry[key] for key in ("language", "build_mode", "os")}
            for entry in sorted(codeql.values(), key=lambda entry: entry["order"])
        ],
        "npm_scripts": npm_scripts,
        "npm_dev": npm_dev,
    }
