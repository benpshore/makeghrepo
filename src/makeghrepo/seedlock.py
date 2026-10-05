"""Validate the packaged Python bootstrap inputs as data, without invoking uv.

This deliberately accepts one fixed dependency contract. It is not a resolver,
an arbitrary-project verifier, or an OS sandbox. Trusted CI checks the seed with
uv; the installed generator checks that the manifest and graph still match it.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from copy import deepcopy
from importlib.resources import files
from pathlib import Path

from makeghrepo import names

SEED_NAME = "makeghrepo-seed"
PYTHON_COMPONENTS = frozenset({"python", "api", "sqlite"})


def require_supported(languages: list[str]) -> None:
    """Fail before bootstrap for stacks without a validated packaged lock plan."""
    if languages and not (
        "python" in languages and set(languages) <= PYTHON_COMPONENTS
    ):
        raise ValueError(
            "Packaged bootstrap currently supports python with optional sqlite/api, "
            "or an empty language selection. This combination needs another lock plan. "
            "Use --render DIR to generate its files without running package code."
        )


def manifest_contract(manifest: dict, name: str, *, lib: bool) -> dict:
    """Normalize only known project identity fields; preserve every dependency setting."""
    result = deepcopy(manifest)
    project = result["project"]
    if project["name"] != name or name != names.normalize_name(name):
        raise ValueError("Python manifest identity does not match the bootstrap project")
    project["name"] = SEED_NAME
    if not isinstance(project["description"], str):
        raise ValueError("Python description must be a string")
    project["description"] = "<description>"
    authors = project["authors"]
    if (
        not isinstance(authors, list)
        or len(authors) != 1
        or set(authors[0]) != {"name"}
        or not isinstance(authors[0]["name"], str)
    ):
        raise ValueError("Python authors do not match the packaged template")
    authors[0]["name"] = "<author>"
    if "license" in project:
        if project.pop("license") != "MIT":
            raise ValueError("Python license does not match the packaged template")
    expected_scripts = None if lib else {name: f"{names.package_name(name)}:main"}
    if project.pop("scripts", None) != expected_scripts:
        raise ValueError("Python entry points do not match the recorded library choice")
    wheel = result["tool"]["hatch"]["build"]["targets"]["wheel"]
    if wheel["packages"] != [f"src/{names.package_name(name)}"]:
        raise ValueError("Python package path does not match the bootstrap project")
    wheel["packages"] = ["src/makeghrepo_seed"]
    return result


def lock_contract(lock: dict, name: str) -> dict:
    """Normalize the single editable root; retain all versions, sources and hashes."""
    result = deepcopy(lock)
    roots = [p for p in result["package"] if p.get("source") == {"editable": "."}]
    if len(roots) != 1 or roots[0]["name"] != name:
        raise ValueError("Python lock must contain exactly the expected editable root")
    root = roots[0]
    if any(package is not root and package["name"] == name for package in result["package"]):
        raise ValueError("Project name conflicts with a packaged Python dependency")
    root["name"] = SEED_NAME
    result["package"].sort(key=lambda package: package["name"])
    return result


def digest(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _read_toml(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError(f"{path.name} must be a regular file smaller than 1 MB")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def validate_bootstrap(dest: Path, languages: list[str], name: str, *, lib: bool) -> None:
    """Check generated/resumed bootstrap data against the reviewed seed before publication.

    Missing, edited or incompatible inputs stop bootstrap; this never repairs a
    manifest, resolves dependencies, runs a tool, or treats a resume marker as trust.
    Published repositories bypass this bootstrap-only check so retries keep working.
    """
    require_supported(languages)
    if not languages:
        return
    contract = json.loads(
        (files("makeghrepo") / "data/python-seed.json").read_text(encoding="utf-8")
    )
    try:
        manifest = manifest_contract(_read_toml(dest / "pyproject.toml"), name, lib=lib)
        lock = lock_contract(_read_toml(dest / "uv.lock"), name)
        if manifest != contract["manifest"] or digest(lock) != contract["lock_sha256"]:
            raise ValueError("manifest or lock differs from the packaged Python dependency contract")
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError(
            "Cannot bootstrap this Python project with the packaged lock. "
            "Its manifest/lock is missing, edited, or incompatible; no package code was run. "
            "Keep the local project and review the differences before retrying."
        ) from exc
