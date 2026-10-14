"""Transactional offline generation; rollback is scoped to this invocation.

The writer lock serializes cooperating beta processes. It cannot prevent a
different program changing the filesystem. Never delete an existing daughter
or auto-break a lock whose writer might still be running.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from makeghrepo_beta.journal import Journal
from makeghrepo_beta.plan import Plan, directory_state, revalidate


class ExecutionError(RuntimeError):
    """An execution failure with phase and observed installation outcome."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        outcome: str,
        retained: tuple[Path, ...],
        unknown_paths: tuple[Path, ...] = (),
    ):
        super().__init__(message)
        self.phase = phase
        self.outcome = outcome
        self.retained = retained
        self.unknown_paths = unknown_paths


class ExecutionInterrupted(KeyboardInterrupt):
    """Preserve interruption exit semantics while reporting completed effects."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        outcome: str,
        retained: tuple[Path, ...],
        unknown_paths: tuple[Path, ...] = (),
    ):
        super().__init__(message)
        self.phase = phase
        self.outcome = outcome
        self.retained = retained
        self.unknown_paths = unknown_paths


def render_packaged(dest: Path, data: dict[str, object]) -> None:
    # Lazy: dry-run/help need neither Copier nor Git nor any external command.
    from makeghrepo import scaffold

    scaffold.render(dest, data)


def execute(
    plan: Plan, render: Callable[[Path, dict[str, object]], None] = render_packaged
) -> Path:
    """Install a complete render; retain and report effects if completion is uncertain."""
    revalidate(plan)
    dest = plan.destination
    journal = Journal(plan)
    created = []
    stage = None
    stage_state = None
    lock = None
    lock_state = None
    reservation = None
    reservation_attempted = False
    installed = False
    promotion_attempted = False
    uncertain = False
    cleanup_errors: list[Path] = []
    cleanup_unknown: list[Path] = []
    cleanup_diagnostics: list[str] = []
    phase = "persist initial recovery record"
    failure = None
    interrupted = False
    try:
        journal.write(initial=True)
        phase = "prepare parents"
        # Remember only directories actually created here; do not remove parents
        # that already existed or that another invocation won the race to create.
        for parent in reversed(dest.parents):
            if os.path.lexists(parent):
                directory_state(parent)
                continue
            # Record the intended path BEFORE mkdir: the syscall can succeed
            # immediately before KeyboardInterrupt is delivered. Without a
            # confirmed identity it is retained and named, never guessed owned.
            journal.event("create_parent", parent, "attempted")
            created.append((parent, None))
            try:
                parent.mkdir()
            except FileExistsError:
                created.pop()
                directory_state(parent)
            else:
                created[-1] = (parent, directory_state(parent))
                journal.event("create_parent", parent, "confirmed", state=created[-1][1])
        phase = "acquire writer lock"
        key = hashlib.sha256(os.fsencode(dest)).hexdigest()[:20]
        candidate = dest.parent / f".makeghrepo-beta-{key}.lock"
        journal.event("acquire_lock", candidate, "attempted")
        lock = candidate
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError as exc:
            lock = None  # A different writer owns it; never attempt cleanup.
            raise RuntimeError(
                f"another render may be active: {candidate}. "
                "Do not remove this lock until its writer has stopped"
            ) from exc
        lock_state = directory_state(lock)
        journal.event("acquire_lock", lock, "confirmed", state=lock_state)
        phase = "revalidate destination"
        revalidate(plan)
        phase = "create staging directory"
        # Unlike mkdtemp, the chosen path is known before the creation call.
        stage = dest.parent / f".makeghrepo-beta-stage-{uuid.uuid4().hex}"
        journal.event("create_stage", stage, "attempted")
        try:
            stage.mkdir(mode=0o700)
        except FileExistsError:
            stage = None  # An extraordinarily unlikely collision is not ours.
            raise
        stage_state = directory_state(stage)
        journal.event("create_stage", stage, "confirmed", state=stage_state)
        phase = "render templates"
        journal.event("render", stage, "attempted", state=stage_state)
        render(stage, plan.template_data())
        journal.event("render", stage, "confirmed", state=stage_state)
        phase = "validate completed render"
        if directory_state(stage) != stage_state:
            raise RuntimeError("staging directory identity changed; refusing to install it")
        revalidate(plan)
        if plan.previous is None:
            # Reserve the absent name without replacing another writer's path.
            # replace below may replace only an empty directory; a new file or
            # nonempty directory from another program makes it fail safely.
            phase = "reserve destination"
            journal.event("reserve", dest, "attempted")
            reservation_attempted = True
            try:
                dest.mkdir()
            except FileExistsError:
                reservation_attempted = False
                raise
            reservation = directory_state(dest)
            journal.event("reserve", dest, "confirmed", state=reservation)
        else:
            phase = "preserve destination permissions"
            journal.event("permissions", stage, "attempted", state=stage_state)
            os.chmod(stage, dest.stat().st_mode & 0o777)
            journal.event("permissions", stage, "confirmed", state=stage_state)
        phase = "promote rendered output"
        journal.event("promote", dest, "attempted", state=stage_state, source=stage)
        promotion_attempted = True
        os.replace(stage, dest)
        installed = True
        journal.event("promote", dest, "confirmed", state=stage_state, source=stage)
        phase = "cleanup"
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
        interrupted = isinstance(exc, KeyboardInterrupt)
    finally:
        # An interrupt may arrive after rename took effect but before the next
        # Python assignment. Inspect directory identity instead of reporting a
        # false rollback or deleting the completed result as a reservation.
        if promotion_attempted and not installed:
            try:
                stage_present = stage is not None and os.path.lexists(stage)
                installed = not stage_present and directory_state(dest) == stage_state
                uncertain = not stage_present and not installed
            except (OSError, ValueError, KeyboardInterrupt) as exc:
                uncertain = True
                if isinstance(exc, KeyboardInterrupt):
                    interrupted = True
                    failure = failure or exc
        # Cleanup never follows a symlink or deletes a directory whose identity
        # changed. rmdir deliberately preserves anything somebody added.
        pending = []
        if stage is not None:
            pending.append((stage, stage_state, True))
        if reservation_attempted and not installed and not uncertain:
            pending.append((dest, reservation, False))
        if lock is not None:
            pending.append((lock, lock_state, False))
        if not installed and not uncertain:
            pending.extend((path, state, False) for path, state in reversed(created))
        def observe_remaining(path):
            # lexists() turns permission/readback errors into False. Distinguish
            # an observed absence from a path whose current state is unknown.
            try:
                path.lstat()
            except FileNotFoundError:
                return
            except OSError:
                cleanup_unknown.append(path)
            else:
                cleanup_errors.append(path)

        try:
            for path, state, recursive in pending:
                cleanup_step = "inspect directory"
                try:
                    try:
                        current = directory_state(path)
                    except FileNotFoundError:
                        continue
                    if current != state:
                        raise RuntimeError("directory identity changed or was not confirmed")
                    cleanup_step = "record cleanup intent"
                    journal.event("cleanup", path, "attempted", state=state)
                    cleanup_step = "remove directory"
                    if recursive:
                        shutil.rmtree(path)
                    else:
                        path.rmdir()
                    cleanup_step = "record cleanup confirmation"
                    journal.event("cleanup", path, "confirmed", state=state)
                except (OSError, ValueError, RuntimeError) as exc:
                    if failure is None:
                        failure = exc
                        phase = "cleanup"
                    cleanup_diagnostics.append(f"{cleanup_step} failed for {path}: {exc}")
                    observe_remaining(path)
        except KeyboardInterrupt as exc:
            # Stop destructive cleanup, then distinguish present, absent and
            # unknown paths even when a removal completed before interruption.
            interrupted = True
            failure = failure or exc
            phase = "cleanup"
            cleanup_errors = []
            cleanup_unknown = []
            for path, _, _ in pending:
                try:
                    observe_remaining(path)
                except KeyboardInterrupt:
                    cleanup_unknown.append(path)
    outcome = "installed" if installed else "uncertain" if uncertain else "not installed"
    try:
        journal.finish(outcome)
    except (Exception, KeyboardInterrupt) as exc:
        if failure is None:
            phase = "persist recovery outcome"
            failure = exc
        interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
    if failure is not None or cleanup_errors or cleanup_unknown:
        effect = (
            f"Rendered output installed at {dest}"
            if installed
            else f"Installation outcome uncertain at {dest}; inspect before retrying"
            if uncertain
            else f"This invocation did not install rendered output at {dest}"
        )
        status = "interrupted" if interrupted else "failed"
        message = f"Render {status} during {phase}. {effect}. Recovery record: {journal.path}."
        if cleanup_errors:
            message += f" Retained paths need inspection: {', '.join(map(str, cleanup_errors))}."
        if cleanup_unknown:
            message += (
                " Paths with unknown existence need inspection: "
                + ", ".join(map(str, cleanup_unknown))
                + "."
            )
        if cleanup_diagnostics:
            message += " Cleanup errors: " + "; ".join(cleanup_diagnostics) + "."
        if failure is not None:
            message += f" Original failure: {failure or type(failure).__name__}"
        if interrupted:
            raise ExecutionInterrupted(
                message,
                phase=phase,
                outcome=outcome,
                retained=tuple(cleanup_errors),
                unknown_paths=tuple(cleanup_unknown),
            ) from failure
        raise ExecutionError(
            message,
            phase=phase,
            outcome=outcome,
            retained=tuple(cleanup_errors),
            unknown_paths=tuple(cleanup_unknown),
        ) from failure
    return journal.path
