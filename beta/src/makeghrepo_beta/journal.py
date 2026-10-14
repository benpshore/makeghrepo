"""Bounded local effect records and read-only inspection; never a replay authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from makeghrepo_beta import package_version
from makeghrepo_beta.plan import Plan, absolute_path, directory_state

MAX_BYTES = 262_144
MAX_RECORDS = 128
OPERATIONS = {
    "create_parent", "acquire_lock", "create_stage", "render", "reserve",
    "permissions", "promote", "cleanup",
}


def prefix(destination: Path) -> str:
    key = hashlib.sha256(os.fsencode(destination)).hexdigest()[:20]
    return f".makeghrepo-beta-record-{key}."


def identity(path: Path) -> list[int]:
    state = directory_state(path)
    return [state.device, state.inode]


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Journal:
    def __init__(self, plan: Plan):
        parent = plan.destination.parent
        while not os.path.lexists(parent):
            parent = parent.parent
        directory_state(parent)
        invocation = uuid4().hex
        self.path = parent / f"{prefix(plan.destination)}{invocation}.json"
        self.file_state = None
        self.data = {
            "schema": 1,
            "invocation": invocation,
            "destination": str(plan.destination),
            "plan_sha256": hashlib.sha256(plan.as_json().encode()).hexdigest(),
            "generator": f"makeghrepo-beta {package_version()}",
            "started_at": datetime.now(UTC).isoformat(),
            "outcome": None,
            "events": [],
        }
        self.write(initial=True)

    def write(self, *, initial=False):
        encoded = (json.dumps(self.data, sort_keys=True, indent=2) + "\n").encode()
        if len(encoded) > MAX_BYTES:
            raise RuntimeError(f"Recovery record size limit reached: {self.path}")
        target = self.path if initial else self.path.with_suffix(".json.pending")
        try:
            if not initial:
                info = self.path.lstat()
                if (info.st_dev, info.st_ino) != self.file_state or not stat.S_ISREG(info.st_mode):
                    raise RuntimeError("recovery record identity changed")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if not initial:
                os.replace(target, self.path)
            info = self.path.lstat()
            self.file_state = (info.st_dev, info.st_ino)
            sync_directory(self.path.parent)
        except KeyboardInterrupt as exc:
            raise KeyboardInterrupt(f"Recovery record interrupted; inspect {self.path} and {target}") from exc
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(f"Recovery record write failed; inspect {self.path} and {target}") from exc

    def event(self, operation: str, path: Path, status: str, *, state=None, source=None):
        event = {"operation": operation, "path": str(path), "status": status}
        if state is not None:
            event["identity"] = [state.device, state.inode]
        if source is not None:
            event["source"] = str(source)
        self.data["events"].append(event)
        self.write()

    def finish(self, outcome: str):
        self.data.update(outcome=outcome, finished_at=datetime.now(UTC).isoformat())
        self.write()


def _read(path: Path, destination: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("record is not a bounded regular file")
        data = json.loads(stream.read(MAX_BYTES + 1))
    if (
        not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1
        or data.get("destination") != str(destination)
        or not isinstance(data.get("events"), list) or len(data["events"]) > 2048
        or not isinstance(data.get("invocation"), str)
        or not re.fullmatch(r"[a-f0-9]{32}", data["invocation"])
        or path.name not in {
            f"{prefix(destination)}{data['invocation']}.json",
            f"{prefix(destination)}{data['invocation']}.json.pending",
        }
        or data.get("outcome") not in (None, "installed", "not installed", "uncertain")
    ):
        raise ValueError("unsupported or malformed recovery record")
    for event in data["events"]:
        if (
            not isinstance(event, dict) or event.get("operation") not in OPERATIONS
            or event.get("status") not in ("attempted", "confirmed")
            or not isinstance(event.get("path"), str)
        ):
            raise ValueError("malformed effect record")
        allowed = {destination, *destination.parents}
        for key in ("path", "source"):
            if key not in event:
                continue
            if not isinstance(event[key], str):
                raise ValueError("malformed effect path")
            target = absolute_path(Path(event[key]))
            sibling = target.parent == destination.parent and (
                re.fullmatch(r"\.makeghrepo-beta-stage-[a-f0-9]{32}", target.name)
                or re.fullmatch(r"\.makeghrepo-beta-[a-f0-9]{20}\.lock", target.name)
            )
            if str(target) != event[key] or (target not in allowed and not sibling):
                raise ValueError("effect path outside the destination scope")
        if "identity" in event and (
            not isinstance(event["identity"], list) or len(event["identity"]) != 2
            or any(type(value) is not int or value < 0 for value in event["identity"])
        ):
            raise ValueError("malformed directory identity")
    return data


def inspect_records(destination: Path) -> dict[str, object]:
    # Never follow an ancestor symlink while discovering or observing records.
    for parent in reversed(destination.parents):
        if os.path.lexists(parent):
            directory_state(parent)
    records = []
    observations = []
    for parent in destination.parents:
        if not parent.is_dir():
            continue
        for path in parent.glob(prefix(destination) + "*.json*"):
            if len(records) >= MAX_RECORDS:
                raise ValueError("too many recovery records; inspection is bounded at 128")
            entry = {"record": str(path), "recorded_outcome": None, "observation": "unknown"}
            records.append(entry)
            try:
                data = _read(path, destination)
            except (OSError, ValueError, TypeError, RecursionError):
                entry["observation"] = "invalid_or_unreadable_record"
                continue
            entry["recorded_outcome"] = data["outcome"]
            retained = []
            for target in sorted({event["path"] for event in data["events"]}):
                if os.path.lexists(target):
                    retained.append(target)
            entry["retained_paths"] = retained
            promotion = [event for event in data["events"] if event["operation"] == "promote"]
            if promotion:
                event = promotion[-1]
                try:
                    if identity(destination) == event.get("identity"):
                        entry["observation"] = "destination_matches_staged_identity"
                    else:
                        entry["observation"] = "destination_changed_or_unknown"
                except (OSError, ValueError):
                    entry["observation"] = "destination_absent_or_inaccessible"
            else:
                entry["observation"] = "no_promotion_recorded"
    for entry in sorted(records, key=lambda item: item["record"]):
        observations.append(dict(
            capability="recovery", status=entry["observation"],
            detail=f"Recorded outcome: {entry['recorded_outcome'] or 'unfinished'}. Record:\n{entry['record']}",
        ))
        for path in entry.get("retained_paths", []):
            observations.append(dict(capability="retained", status="inspect", detail=f"Path:\n{path}"))
    return {
        "schema": 1, "operation": "recovery-inspection", "destination": str(destination),
        "records": sorted(records, key=lambda item: item["record"]), "observations": observations,
        "limits": [
            "Records are untrusted local observations, not authority to delete or replay.",
            "Matching directory identity is not proof of unchanged contents or ownership.",
            "No records found does not prove that no effects occurred.",
            "No cleanup, retry, lock removal or network requests were performed.",
        ],
    }
