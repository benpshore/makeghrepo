"""Read-only validation shared by dry-run and every supported beta execution.

No renderer, Git client, network client, or subprocess is imported here. Plans
are Python values, not executable commands or a trusted input-file format.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from makeghrepo import names, registry

LANGS = registry.load()
ALIASES = {alias: lang.id for lang in LANGS.values() for alias in lang.aliases}


@dataclass(frozen=True)
class Request:
    words: tuple[str, ...] = ()
    render: Path | None = None
    private: bool = False
    lib: bool = False
    license: str = "none"
    owner: str = "someone"
    author: str = "Test User"
    description: str = "a test project"
    year: str = "2026"


@dataclass(frozen=True)
class DirectoryState:
    device: int
    inode: int


def directory_state(path: Path) -> DirectoryState:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"{path} must be a directory, not a symlink or file")
    return DirectoryState(info.st_dev, info.st_ino)


def destination_state(path: Path) -> DirectoryState | None:
    """Validate every existing ancestor, including dangling symlinks."""
    for part in (*reversed(path.parents), path):
        if os.path.lexists(part):
            directory_state(part)
    if not os.path.lexists(path):
        return None
    state = directory_state(path)
    if any(path.iterdir()):
        raise ValueError(f"{path} already exists and is not empty")
    return state


@dataclass(frozen=True)
class Plan:
    destination: Path
    previous: DirectoryState | None
    name: str
    languages: tuple[str, ...]
    private: bool
    lib: bool
    license: str
    owner: str
    author: str
    description: str
    year: str

    def template_data(self) -> dict[str, object]:
        return {
            "project_name": self.name,
            "package_name": names.package_name(self.name),
            "description": self.description,
            "author_name": self.author,
            "github_owner": self.owner,
            "year": self.year,
            "private": self.private,
            "py_lib": self.lib,
            "project_license": self.license,
            "languages": list(self.languages),
        }

    def as_json(self) -> str:
        return json.dumps(
            {
                "schema": 1,
                "distribution": "makeghrepo-beta",
                "operation": "offline-render",
                "destination": str(self.destination),
                "destination_exists": self.previous is not None,
                "inputs": self.template_data(),
                "steps": [
                    "revalidate destination and acquire local writer lock",
                    "render packaged templates in an owned staging directory",
                    "revalidate destination and atomically install rendered files",
                    "remove owned staging files and release writer lock",
                ],
                "limits": [
                    "No GitHub capability, authentication, or name-availability checks.",
                    "No dependencies installed, project checks run, or Git repository created.",
                    "Destination and filesystem availability can change after planning.",
                ],
            },
            indent=2,
            sort_keys=True,
        )


def _language(word: str) -> str | None:
    word = word.lower()
    return word if word in LANGS else ALIASES.get(word)


def _text(value: str, label: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} must not contain control characters")
    return value


def build_plan(request: Request) -> Plan:
    """Validate all inputs and local destination state before allowing effects."""
    words = list(request.words)
    raw_name = words.pop(0) if words and _language(words[0]) is None else "quiet-otter"
    _text(raw_name, "name")
    if any(char in raw_name for char in ("/", "\\")):
        raise ValueError("name must not contain path separators")
    name = names.validate_name(names.normalize_name(raw_name))
    languages: list[str] = []
    for word in words:
        lang = _language(word)
        if lang is None:
            raise ValueError(f"unknown language {word!r}. Choose from: {', '.join(LANGS)}")
        if lang not in languages:
            languages.append(lang)
    for lang in languages:
        if LANGS[lang].leading_letter and not name[0].isalpha():
            raise ValueError(f"{name!r} can't be a {lang} package name: start with a letter")
    if request.lib and "python" not in languages:
        raise ValueError("--lib only applies to python; add python to the language list")
    if request.license not in ("none", "MIT"):
        raise ValueError("--license must be none or MIT")
    owner = names.validate_owner(request.owner)
    author = _text(request.author, "author")
    description = _text(request.description, "description")
    if len(request.year) != 4 or not request.year.isascii() or not request.year.isdigit():
        raise ValueError("--year must be four ASCII digits")
    if request.render is None:
        raise ValueError(
            "this beta increment requires --render DIR; GitHub creation and its capability "
            "preflight are not implemented in beta yet. Stable makeghrepo is unchanged"
        )
    _text(str(request.render), "destination")
    # absolute() normalizes relative spelling without hiding a symlink like resolve().
    destination = request.render.expanduser().absolute()
    if ".." in destination.parts:
        raise ValueError("destination must not contain '..' path components")
    previous = destination_state(destination)
    return Plan(
        destination=destination,
        previous=previous,
        name=name,
        languages=tuple(languages),
        private=request.private,
        lib=request.lib,
        license=request.license,
        owner=owner,
        author=author,
        description=description,
        year=request.year,
    )


def revalidate(plan: Plan) -> None:
    if destination_state(plan.destination) != plan.previous:
        raise ValueError(f"{plan.destination} changed after planning; run the command again")


def describe(plan: Plan) -> str:
    data = json.loads(plan.as_json())
    return "\n".join(
        [
            f"Plan: offline render to {plan.destination}",
            f"Project: {plan.owner}/{plan.name}",
            f"Languages: {', '.join(plan.languages) or 'none'}; license: {plan.license}",
            f"Visibility metadata: {'private' if plan.private else 'public'}",
            *[f"{i}. {step}" for i, step in enumerate(data["steps"], 1)],
            *data["limits"],
        ]
    )
