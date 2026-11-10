"""Local write-ahead state for one repository's Project creation.

This fences cooperating writers using the same retained state file. It is not a
provider idempotency key or a cross-machine lock. Never erase uncertain intent
merely because a remote search is empty.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

FILENAME = "makeghrepo-project.json"


class ProjectStateError(RuntimeError):
    pass


def _sync_directory(path: Path) -> None:
    # POSIX directory durability matters when an intent is first installed or
    # replaced. Other platforms retain atomic replace and file fsync semantics.
    if hasattr(os, "O_DIRECTORY"):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _positive_number(value: object) -> bool:
    return type(value) is int and value > 0


class ProjectState:
    def __init__(self, path: Path, repo: str, org: bool) -> None:
        self.path = path
        self.lock = path.with_name(path.name + ".lock")
        self.repo = repo
        self.org = org
        # A missing journal may belong to an older or interrupted invocation.
        # Only explicit fresh initialization can establish no prior attempt.
        self.attempted = True
        self.number: int | None = None
        self._exists = False
        self._lock_identity: tuple[int, int] | None = None

    def __enter__(self) -> ProjectState:
        try:
            fd = os.open(self.lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ProjectStateError(
                f"Project setup is locked at {self.lock}; another invocation may be running "
                "or interrupted. Inspect it before removing the lock."
            ) from exc
        try:
            with os.fdopen(fd, "w") as stream:
                info = os.fstat(stream.fileno())
                self._lock_identity = (info.st_dev, info.st_ino)
                stream.write(f"{self.repo}\npid={os.getpid()}\n")
                stream.flush()
                os.fsync(stream.fileno())
            _sync_directory(self.path.parent)
            self._load()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args: object) -> None:
        try:
            info = self.lock.lstat()
        except FileNotFoundError:
            self._lock_identity = None
            return
        if (info.st_dev, info.st_ino) == self._lock_identity:
            self.lock.unlink()
            _sync_directory(self.path.parent)
        self._lock_identity = None

    def _load(self) -> None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode):
            raise ProjectStateError(f"Project state must be a regular file: {self.path}")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ProjectStateError(f"Unreadable Project state retained at {self.path}") from exc
        if (
            not isinstance(data, dict)
            or type(data.get("schema")) is not int
            or data.get("schema") != 1
            or type(data.get("org")) is not bool
            or data["org"] != self.org
            or not isinstance(data.get("repo"), str)
            or data["repo"].casefold() != self.repo.casefold()
            or data.get("phase") not in {"ready", "attempted", "identified"}
            or set(data) != {"schema", "repo", "org", "phase", "number"}
            or (data["phase"] != "identified" and data["number"] is not None)
            or (data["phase"] == "identified" and not _positive_number(data["number"]))
        ):
            raise ProjectStateError(f"Invalid or mismatched Project state retained at {self.path}")
        self._exists = True
        self.attempted = data["phase"] != "ready"
        self.number = data["number"]

    def initialize(self) -> None:
        """Called only while initializing a newly rendered local project."""
        if self._exists:
            raise ProjectStateError("Refusing to reset existing Project recovery state")
        self._save("ready", None)

    def save(self, number: int | None = None) -> None:
        self._save("attempted" if number is None else "identified", number)

    def _save(self, phase: str, number: int | None) -> None:
        if self._lock_identity is None:
            raise ProjectStateError("Project state must be locked before saving")
        info = self.lock.lstat()
        if (info.st_dev, info.st_ino) != self._lock_identity:
            raise ProjectStateError("Project state lock changed; refusing to save")
        if number is not None and not _positive_number(number):
            raise ProjectStateError("Project number must be a positive integer")
        data = {
            "schema": 1,
            "repo": self.repo,
            "org": self.org,
            "phase": phase,
            "number": number,
        }
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        pending = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, self.path)
            _sync_directory(self.path.parent)
        finally:
            pending.unlink(missing_ok=True)
        self._exists = True
        self.attempted = phase != "ready"
        self.number = number
