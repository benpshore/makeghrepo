"""Transactional offline generation; rollback is scoped to this invocation.

The writer lock serializes cooperating beta processes. It cannot prevent a
different program changing the filesystem. Never delete an existing daughter
or auto-break a lock whose writer might still be running.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from makeghrepo_beta.plan import Plan, directory_state, revalidate


class ExecutionError(RuntimeError):
    """An execution failure with phase and observed installation outcome."""

    def __init__(self, message: str, *, phase: str, outcome: str, retained: tuple[Path, ...]):
        super().__init__(message)
        self.phase = phase
        self.outcome = outcome
        self.retained = retained


class ExecutionInterrupted(KeyboardInterrupt):
    """Preserve interruption exit semantics while reporting completed effects."""


def render_packaged(dest: Path, data: dict[str, object]) -> None:
    # Lazy: dry-run/help need neither Copier nor Git nor any external command.
    from makeghrepo import scaffold

    scaffold.render(dest, data)


def execute(
    plan: Plan, render: Callable[[Path, dict[str, object]], None] = render_packaged
) -> None:
    """Install a complete render; retain and report effects if completion is uncertain."""
    revalidate(plan)
    dest = plan.destination
    created = []
    stage = None
    stage_state = None
    lock = None
    lock_state = None
    reservation = None
    reservation_created = False
    installed = False
    promotion_attempted = False
    uncertain = False
    cleanup_errors: list[Path] = []
    phase = "prepare parents"
    failure = None
    try:
        # Remember only directories actually created here; do not remove parents
        # that already existed or that another invocation won the race to create.
        for parent in reversed(dest.parents):
            try:
                parent.mkdir()
            except FileExistsError:
                directory_state(parent)
            else:
                created.append((parent, None))
                created[-1] = (parent, directory_state(parent))
        phase = "acquire writer lock"
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
        phase = "revalidate destination"
        revalidate(plan)
        phase = "create staging directory"
        stage = Path(tempfile.mkdtemp(prefix=".makeghrepo-beta-stage-", dir=dest.parent))
        stage_state = directory_state(stage)
        phase = "render templates"
        render(stage, plan.template_data())
        phase = "validate completed render"
        if directory_state(stage) != stage_state:
            raise RuntimeError("staging directory identity changed; refusing to install it")
        revalidate(plan)
        if plan.previous is None:
            # Reserve the absent name without replacing another writer's path.
            # replace below may replace only an empty directory; a new file or
            # nonempty directory from another program makes it fail safely.
            phase = "reserve destination"
            dest.mkdir()
            reservation_created = True
            reservation = directory_state(dest)
        else:
            phase = "preserve destination permissions"
            os.chmod(stage, dest.stat().st_mode & 0o777)
        phase = "promote rendered output"
        promotion_attempted = True
        os.replace(stage, dest)
        installed = True
        phase = "cleanup"
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
    finally:
        # An interrupt may arrive after rename took effect but before the next
        # Python assignment. Inspect directory identity instead of reporting a
        # false rollback or deleting the completed result as a reservation.
        if promotion_attempted and not installed:
            try:
                stage_present = stage is not None and os.path.lexists(stage)
                installed = not stage_present and directory_state(dest) == stage_state
                uncertain = not stage_present and not installed
            except (OSError, ValueError):
                uncertain = True
        # Cleanup never follows a symlink or deletes a directory whose identity
        # changed. rmdir deliberately preserves anything somebody added.
        pending = []
        if stage is not None:
            pending.append((stage, stage_state, True))
        if reservation_created and not installed and not uncertain:
            pending.append((dest, reservation, False))
        if lock is not None:
            pending.append((lock, lock_state, False))
        if not installed and not uncertain:
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
                cleanup_errors.append(path)
    if failure is not None or cleanup_errors:
        outcome = "installed" if installed else "uncertain" if uncertain else "not installed"
        effect = (
            f"Rendered output installed at {dest}"
            if installed
            else f"Installation outcome uncertain at {dest}; inspect before retrying"
            if uncertain
            else f"This invocation did not install rendered output at {dest}"
        )
        message = f"Render failed during {phase}. {effect}."
        if cleanup_errors:
            message += f" Retained paths need inspection: {', '.join(map(str, cleanup_errors))}."
        if failure is not None:
            message += f" Original failure: {failure or type(failure).__name__}"
        if isinstance(failure, KeyboardInterrupt):
            raise ExecutionInterrupted(message) from failure
        raise ExecutionError(
            message, phase=phase, outcome=outcome, retained=tuple(cleanup_errors)
        ) from failure
