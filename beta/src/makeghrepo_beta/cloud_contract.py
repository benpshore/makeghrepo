"""Closed, data-only contracts for one prebuilt Python starter.

Repository identity is configuration. Package identity and built bytes are not
substitution sites. No commands, URLs, permission grants or host probes are inputs.
"""

from __future__ import annotations

import base64
import configparser
import csv
import hashlib
import io
import json
import os
import re
import stat
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from email.parser import BytesParser
from importlib.resources import files
from pathlib import Path, PurePosixPath

from makeghrepo import names
from makeghrepo_beta import seedlock
from makeghrepo_beta.plan import directory_state

REPOSITORY = "benpshore/makeghrepo"
WORKFLOW = ".github/workflows/ci.yml"
PACKAGE = "makeghrepo-seed"
WHEEL = "dist/makeghrepo_seed-0.0.0-py3-none-any.whl"
PROFILE = {
    "id": "python-console-linux-x86_64-v1",
    "runner": "ubuntu-24.04",
    "python": "3.14.8",
    "uv": "0.12.23",
    "package": PACKAGE,
    "package_version": "0.0.0",
    "wheel_tag": "py3-none-any",
    "license": "none",
    "build_requirements": ["hatchling==1.32.0", "uv-dynamic-versioning==0.14.0"],
}
CHECKS = ["seed", "lock", "lint", "test", "compile", "build", "installed-smoke"]
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 512
MAX_JSON = 1024 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical(value: object) -> bytes:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (text + "\n").encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        require(key not in result, f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _nonfinite(_value: str) -> None:
    raise ValueError("non-finite JSON numbers are not permitted")


def decode(data: bytes) -> dict:
    require(len(data) <= MAX_JSON, "JSON exceeds size limit")
    value = json.loads(data, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    require(type(value) is dict, "expected a JSON object")
    return value


def exact(value: object, keys: set[str], label: str) -> dict:
    require(type(value) is dict and set(value) == keys, f"invalid {label} fields")
    return value


def integer(value: object, label: str) -> int:
    require(type(value) is int and value > 0, f"invalid {label}")
    return value


def digest_text(value: object, label: str, length: int = 64) -> str:
    require(isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value), label)
    return value


def seed_digest() -> str:
    return sha256((files("makeghrepo_beta") / "assets/python-seed.json").read_bytes())


@dataclass(frozen=True)
class CloudPlan:
    source_sha: str
    owner: str
    name: str

    def __post_init__(self) -> None:
        digest_text(self.source_sha, "source must be a full lowercase commit SHA", 40)
        require(isinstance(self.owner, str), "owner must be text")
        require(isinstance(self.name, str), "name must be text")
        names.validate_owner(self.owner)
        names.validate_name(self.name)
        require(names.normalize_name(self.name) == self.name, "name must be canonical")

    def asset(self) -> dict:
        return {
            "source": {"repository": REPOSITORY, "commit": self.source_sha, "workflow": WORKFLOW},
            "profile": deepcopy(PROFILE),
            "seed_sha256": seed_digest(),
        }

    @property
    def asset_key(self) -> str:
        return sha256(canonical(self.asset()))

    def identity(self) -> dict:
        return {"schema": 1, "repository": f"{self.owner}/{self.name}"}

    def document(self) -> dict:
        return {
            "schema": 1,
            "operation": "consume-prebuilt-python",
            "asset": self.asset(),
            "asset_key": self.asset_key,
            "identity": self.identity(),
            "permissions": {"contents": "read", "actions": "read"},
        }

    @property
    def plan_id(self) -> str:
        return sha256(canonical(self.document()))

    @classmethod
    def parse(cls, data: bytes) -> CloudPlan:
        value = decode(data)
        try:
            owner, name = value["identity"]["repository"].split("/")
            plan = cls(value["asset"]["source"]["commit"], owner, name)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("invalid cloud plan") from exc
        require(canonical(value) == canonical(plan.document()), "plan contract mismatch")
        return plan


def artifact_name(plan: CloudPlan, attempt: int) -> str:
    return f"beta-python-{plan.asset_key}-attempt-{integer(attempt, 'run attempt')}"


def prepare_fetch(plan: CloudPlan, run_id: int, destination: Path) -> dict:
    """The same read-only local validation precedes dry-run and consumption."""
    integer(run_id, "run id")
    destination = destination.expanduser().absolute()
    require(".." not in destination.parts, "destination must not contain '..'")
    require(
        all(ord(char) >= 32 and ord(char) != 127 for char in str(destination)),
        "destination contains a control character",
    )
    for part in (*reversed(destination.parents), destination):
        if os.path.lexists(part):
            directory_state(part)
    if destination.exists() and any(destination.iterdir()):
        receipt = destination / ".makeghrepo-cloud.json"
        require(
            receipt.is_file() and not receipt.is_symlink() and receipt.stat().st_size <= MAX_JSON,
            "destination is not a recorded cloud result; existing work is retained",
        )
        recorded = decode(receipt.read_bytes())
        require(
            recorded.get("plan_id") == plan.plan_id
            and canonical(recorded.get("plan")) == canonical(plan.document())
            and type(recorded.get("run_id")) is int
            and recorded["run_id"] == run_id,
            "destination belongs to a different plan/run; existing work is retained",
        )
    return {
        "plan": plan.document(),
        "run_id": run_id,
        "destination": str(destination),
        "remote_state": "unverified until authenticated readback",
    }


def validate_run(plan: CloudPlan, run: dict, run_id: int) -> int:
    require(type(run) is dict, "missing run metadata")
    require(integer(run.get("id"), "run id") == run_id, "run id mismatch")
    require(run.get("head_sha") == plan.source_sha, "run source mismatch")
    require(run.get("path") == WORKFLOW, "unexpected workflow")
    require(run.get("status") == "completed", "workflow has not completed; retry this run")
    require(run.get("conclusion") == "success", "workflow did not pass all gates")
    for field in ("repository", "head_repository"):
        repo = run.get(field)
        require(type(repo) is dict and repo.get("full_name") == REPOSITORY, "untrusted repository")
        integer(repo.get("id"), "repository id")
    require(run["repository"]["id"] == run["head_repository"]["id"], "fork run refused")
    require(
        run.get("event") == "workflow_dispatch"
        or (run.get("event") == "push" and run.get("head_branch") == "main"),
        "only a reviewed manual run or main push is a consumable source; PR artifacts are tests",
    )
    return integer(run.get("run_attempt"), "run attempt")


def validate_artifact(plan: CloudPlan, run: dict, listing: dict) -> dict:
    attempt = validate_run(plan, run, integer(run.get("id"), "run id"))
    require(type(listing) is dict, "invalid artifact listing")
    artifacts = listing.get("artifacts")
    require(type(artifacts) is list, "missing artifact list")
    require(
        type(listing.get("total_count")) is int and listing["total_count"] == len(artifacts) <= 100,
        "incomplete artifact listing; cannot establish uniqueness",
    )
    require(all(type(item) is dict for item in artifacts), "invalid artifact entry")
    matches = [item for item in artifacts if item.get("name") == artifact_name(plan, attempt)]
    require(len(matches) == 1, "expected exactly one artifact for this source and attempt")
    artifact = matches[0]
    integer(artifact.get("id"), "artifact id")
    require(artifact.get("expired") is False, "artifact has expired or expiry is unknown")
    expiry = artifact.get("expires_at")
    require(isinstance(expiry, str), "missing artifact expiry")
    expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    require(
        expires_at.tzinfo is not None and expires_at > datetime.now(UTC), "artifact has expired"
    )
    size = integer(artifact.get("size_in_bytes"), "artifact size")
    require(size <= MAX_BYTES, "artifact exceeds size limit")
    digest = artifact.get("digest")
    require(isinstance(digest, str) and digest.startswith("sha256:"), "missing artifact digest")
    digest_text(digest[7:], "invalid artifact digest")
    origin = artifact.get("workflow_run")
    require(type(origin) is dict, "missing artifact run provenance")
    for key, expected in {
        "id": run["id"],
        "head_sha": plan.source_sha,
        "repository_id": run["repository"]["id"],
        "head_repository_id": run["repository"]["id"],
    }.items():
        require(
            type(origin.get(key)) is type(expected) and origin[key] == expected,
            f"artifact provenance mismatch: {key}",
        )
    return artifact


def safe_path(name: str) -> None:
    path = PurePosixPath(name)
    require(
        bool(path.parts)
        and not path.is_absolute()
        and str(path) == name
        and not any(part.casefold() in {"..", ".git"} for part in path.parts)
        and "\\" not in name
        and ":" not in name
        and all(32 <= ord(char) < 127 for char in name),
        f"unsafe artifact path: {name!r}",
    )


def read_archive(data: bytes) -> dict[str, tuple[bytes, int]]:
    try:
        return _read_archive(data)
    except (zipfile.BadZipFile, NotImplementedError) as exc:
        raise ValueError("invalid ZIP artifact") from exc


def _read_archive(data: bytes) -> dict[str, tuple[bytes, int]]:
    require(len(data) <= MAX_BYTES, "archive exceeds size limit")
    result = {}
    folded = set()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        require(0 < len(entries) <= MAX_FILES, "invalid archive file count")
        require(sum(item.file_size for item in entries) <= MAX_BYTES, "expanded archive too large")
        for item in entries:
            safe_path(item.filename)
            require(item.filename.casefold() not in folded, "duplicate archive path")
            folded.add(item.filename.casefold())
            mode = item.external_attr >> 16
            require(
                not item.is_dir() and stat.S_IFMT(mode) in {0, stat.S_IFREG},
                "artifact contains a link, directory or special file",
            )
            require(not item.flag_bits & 1, "encrypted artifact refused")
            require(mode & 0o7777 in {0, 0o600, 0o644, 0o755}, "unsafe artifact permissions")
            require(
                item.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                "unsupported archive compression",
            )
            content = archive.read(item)
            require(len(content) == item.file_size, "archive size mismatch")
            result[item.filename] = (content, 0o755 if mode & 0o111 else 0o644)
    return result


def inventory(payload: dict[str, tuple[bytes, int]]) -> dict:
    return {
        name: {"sha256": sha256(content), "bytes": len(content), "mode": mode}
        for name, (content, mode) in sorted(payload.items())
    }


def validate_wheel(data: bytes, source: bytes) -> None:
    """Check identity, source and RECORD as data; never import downloaded code."""
    try:
        _validate_wheel(data, source)
    except (configparser.Error, csv.Error) as exc:
        raise ValueError("invalid wheel metadata") from exc


def _validate_wheel(data: bytes, source: bytes) -> None:
    wheel = read_archive(data)
    metadata = "makeghrepo_seed-0.0.0.dist-info/"
    needed = {f"{metadata}{name}" for name in ("METADATA", "WHEEL", "entry_points.txt", "RECORD")}
    require(needed <= wheel.keys(), "wheel is missing metadata")
    require(
        all(name.startswith(("makeghrepo_seed/", metadata)) for name in wheel),
        "unexpected wheel package identity",
    )
    require(
        {name for name in wheel if name.startswith("makeghrepo_seed/")}
        == {"makeghrepo_seed/__init__.py"},
        "unexpected wheel code",
    )
    message = BytesParser().parsebytes(wheel[metadata + "METADATA"][0])
    identity = {"Name": PACKAGE, "Version": "0.0.0", "Requires-Python": ">=3.14"}
    for field, expected in identity.items():
        require(message.get_all(field) == [expected], f"wheel {field} mismatch")
    require(message.get_all("Requires-Dist") is None, "unexpected runtime dependencies")
    tags = BytesParser().parsebytes(wheel[metadata + "WHEEL"][0])
    require(
        tags.get_all("Tag") == ["py3-none-any"] and tags.get_all("Root-Is-Purelib") == ["true"],
        "incompatible wheel tag",
    )
    entrypoints = configparser.ConfigParser(interpolation=None)
    entrypoints.read_string(wheel[metadata + "entry_points.txt"][0].decode())
    require(
        entrypoints.sections() == ["console_scripts"]
        and dict(entrypoints["console_scripts"]) == {PACKAGE: "makeghrepo_seed:main"},
        "wheel entry point mismatch",
    )
    require(
        wheel.get("makeghrepo_seed/__init__.py", (None,))[0] == source,
        "wheel code differs from validated source",
    )
    recorded = set()
    for row in csv.reader(io.StringIO(wheel[metadata + "RECORD"][0].decode())):
        require(len(row) == 3, "invalid wheel RECORD")
        name, digest, size = row
        require(name in wheel and name not in recorded, "unexpected wheel RECORD path")
        recorded.add(name)
        if name == metadata + "RECORD":
            require(digest == size == "", "invalid RECORD self entry")
        else:
            content = wheel[name][0]
            expected_hash = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
            require(
                digest == "sha256=" + expected_hash.decode() and size == str(len(content)),
                "wheel RECORD integrity mismatch",
            )
    require(recorded == set(wheel), "incomplete wheel RECORD")


def validate_bundle(plan: CloudPlan, data: bytes, run_id: int, attempt: int) -> dict:
    payload = read_archive(data)
    require("result.json" in payload, "missing result contract")
    result = decode(payload["result.json"][0])
    exact(
        result,
        {"schema", "asset", "asset_key", "run_id", "run_attempt", "checks", "files"},
        "result",
    )
    expected = {
        "schema": 1,
        "asset": plan.asset(),
        "asset_key": plan.asset_key,
        "run_id": run_id,
        "run_attempt": attempt,
        "checks": {check: "passed" for check in CHECKS},
        "files": inventory(
            {name: entry for name, entry in payload.items() if name != "result.json"}
        ),
    }
    require(canonical(result) == canonical(expected), "result contract or file integrity mismatch")
    required = {
        "source/pyproject.toml",
        "source/uv.lock",
        "source/src/makeghrepo_seed/__init__.py",
        "source/tests/test_smoke.py",
        "source/README.md",
        "build/requirements.lock",
        WHEEL,
    }
    require(required <= payload.keys(), "incomplete prebuilt starter")
    require(
        all(
            name in {"result.json", "build/requirements.lock", WHEEL} or name.startswith("source/")
            for name in payload
        ),
        "unexpected payload",
    )
    seedlock.validate_contents(
        {name: payload[f"source/{name}"][0] for name in seedlock.PYTHON_INPUTS},
        ["python"],
        PACKAGE,
        lib=False,
    )
    require(bool(payload["build/requirements.lock"][0]), "missing resolved build dependencies")
    validate_wheel(payload[WHEEL][0], payload["source/src/makeghrepo_seed/__init__.py"][0])
    return payload


def verify_download(plan: CloudPlan, run: dict, artifact: dict, archive: bytes) -> dict:
    require(len(archive) == artifact["size_in_bytes"], "download size mismatch")
    require("sha256:" + sha256(archive) == artifact["digest"], "download digest mismatch")
    outer = read_archive(archive)
    require(set(outer) == {"bundle.zip"}, "unexpected GitHub artifact layout")
    return validate_bundle(plan, outer["bundle.zip"][0], run["id"], run["run_attempt"])
