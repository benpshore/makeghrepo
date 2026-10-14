"""Transactional offline generation; rollback is scoped to this invocation.

The writer lock serializes cooperating beta processes. It cannot prevent a
different program changing the filesystem. Never delete an existing daughter
or auto-break a lock whose writer might still be running.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from makeghrepo_beta.plan import Plan, directory_state, revalidate


def render_packaged(dest: Path, data: dict[str, object]) -> None:
    # Lazy: dry-run/help need neither Copier nor Git nor any external command.
    from makeghrepo import scaffold

    scaffold.render(dest, data)


def execute(
    plan: Plan, render: Callable[[Path, dict[str, object]], None] = render_packaged
) -> None:
    """Install a complete render, or preserve the old destination on failure."""
    revalidate(plan)
    dest = plan.destination
    created = []
    stage = None
    stage_state = None
    lock = None
    lock_state = None
    reservation = None
    installed = False
    cleanup_errors = []
    try:
        # Remember only directories actually created here; do not remove parents
        # that already existed or that another invocation won the race to create.
        for parent in reversed(dest.parents):
            try:
                parent.mkdir()
            except FileExistsError:
                directory_state(parent)
            else:
                created.append((parent, directory_state(parent)))
        key = hashlib.sha256(os.fsencode(dest)).hexdigest()[:20]
        candidate = dest.parent / f".makeghrepo-beta-{key}.lock"
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise RuntimeError(
                f"another render may be active: {candidate}. "
                "Do not remove this lock until its writer has stopped"
            ) from exc
        lock = candidate
        lock_state = directory_state(lock)
        revalidate(plan)
        stage = Path(tempfile.mkdtemp(prefix=".makeghrepo-beta-stage-", dir=dest.parent))
        stage_state = directory_state(stage)
        render(stage, plan.template_data())
        revalidate(plan)
        if plan.previous is None:
            # Reserve the absent name without replacing another writer's path.
            # replace below may replace only an empty directory; a new file or
            # nonempty directory from another program makes it fail safely.
            dest.mkdir()
            reservation = directory_state(dest)
        else:
            os.chmod(stage, dest.stat().st_mode & 0o777)
        os.replace(stage, dest)
        installed = True
    finally:
        # Cleanup never follows a symlink or deletes a directory whose identity
        # changed. rmdir deliberately preserves anything somebody added.
        pending = []
        if stage is not None:
            pending.append((stage, stage_state, True))
        if reservation is not None and not installed:
            pending.append((dest, reservation, False))
        if lock is not None:
            pending.append((lock, lock_state, False))
        if not installed:
            pending.extend((path, state, False) for path, state in reversed(created))
        for path, state, recursive in pending:
            try:
                if not os.path.lexists(path):
                    continue
                if directory_state(path) != state:
                    raise RuntimeError("directory identity changed")
                if recursive:
                    shutil.rmtree(path)
                else:
                    path.rmdir()
            except (OSError, ValueError, RuntimeError):
                cleanup_errors.append(str(path))
        if cleanup_errors:
            outcome = (
                f"Rendered output installed at {dest}" if installed else "Render did not complete"
            )
            original = sys.exception()
            detail = f" Original failure: {original}" if original is not None else ""
            raise RuntimeError(
                f"{outcome}; retained paths need inspection: {', '.join(cleanup_errors)}.{detail}"
            )
