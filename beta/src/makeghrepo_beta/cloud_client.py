"""Read-only GitHub orchestration and retry-safe installation of verified bytes."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from makeghrepo_beta.cloud_contract import (
    MAX_BYTES,
    MAX_JSON,
    REPOSITORY,
    CloudPlan,
    canonical,
    decode,
    integer,
    inventory,
    prepare_fetch,
    require,
    validate_artifact,
    validate_run,
    verify_download,
)
from makeghrepo_beta.execute import execute
from makeghrepo_beta.plan import Request, build_plan, directory_state


class GhAPI:
    """All endpoints are constructed internally; never invoke an artifact's commands."""

    def get(self, endpoint: str, limit: int = MAX_JSON) -> bytes:
        expired = threading.Event()
        with subprocess.Popen(
            ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            def stop() -> None:
                expired.set()
                process.kill()

            timer = threading.Timer(30, stop)
            timer.start()
            try:
                data = bytearray()
                while chunk := process.stdout.read(min(65536, limit + 1 - len(data))):
                    data.extend(chunk)
                    if len(data) > limit:
                        process.kill()
                        raise ValueError("GitHub response exceeds size limit")
                status = process.wait()
                require(not expired.is_set(), "GitHub read timed out; retry the same run")
                require(status == 0, f"GitHub read failed for {endpoint}; check gh access and retry")
                return bytes(data)
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()


def _verify_installed(destination: Path, payload: dict) -> None:
    for ancestor in (*reversed(destination.parents), destination):
        directory_state(ancestor)
    found = {}
    expected_dirs = {
        str(parent)
        for name in payload
        for parent in Path(name).parents
        if str(parent) != "."
    }
    for path in destination.rglob("*"):
        relative = str(path.relative_to(destination))
        require(not path.is_symlink(), f"installed path is a symlink: {path}")
        if path.is_dir():
            require(relative in expected_dirs, f"unexpected directory: {path}")
            continue
        require(path.is_file() and relative in payload, f"unexpected installed file: {path}")
        expected, mode = payload[relative]
        require(path.stat().st_size == len(expected), f"installed size changed: {path}")
        require(path.read_bytes() == expected and path.stat().st_mode & 0o777 == mode,
                f"installed file changed: {path}; retained for inspection")
        found[relative] = True
    require(set(found) == set(payload), "installed result is incomplete; retained for inspection")


def install(plan: CloudPlan, payload: dict, artifact: dict, run: dict, destination: Path) -> str:
    """Complete retries read back exact bytes; incomplete/crashed writers are retained."""
    destination = destination.expanduser().absolute()
    require(".." not in destination.parts, "destination must not contain '..'")
    output = dict(payload)
    output["identity.json"] = (canonical(plan.identity()), 0o644)
    receipt = {
        "schema": 1, "plan": plan.document(), "plan_id": plan.plan_id,
        "artifact_id": artifact["id"], "archive_digest": artifact["digest"],
        "run_id": run["id"], "run_attempt": run["run_attempt"],
        "files": inventory(output),
    }
    output[".makeghrepo-cloud.json"] = (canonical(receipt), 0o644)
    if os.path.lexists(destination):
        directory_state(destination)
        if any(destination.iterdir()):
            _verify_installed(destination, output)
            return "already verified"

    local = build_plan(Request(words=("makeghrepo-seed", "python"), render=destination))

    def materialize(stage: Path, _data: dict) -> None:
        for name, (content, mode) in output.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            path.chmod(mode)
        _verify_installed(stage, output)

    execute(local, materialize)
    _verify_installed(destination, output)
    return "verified and installed"


def consume(plan: CloudPlan, run_id: int, destination: Path, api: GhAPI | None = None) -> str:
    # Reconstruct even direct Python callers' values before any remote read.
    plan = CloudPlan.parse(canonical(plan.document()))
    request = prepare_fetch(plan, run_id, destination)
    destination = Path(request["destination"])
    api = api or GhAPI()
    prefix = f"repos/{REPOSITORY}/actions"
    run_endpoint = f"{prefix}/runs/{run_id}"
    run = decode(api.get(run_endpoint))
    attempt = validate_run(plan, run, run_id)
    listing = decode(api.get(f"{run_endpoint}/artifacts?per_page=100"))
    artifact = validate_artifact(plan, run, listing)
    artifact_endpoint = f"{prefix}/artifacts/{artifact['id']}"
    archive = api.get(f"{artifact_endpoint}/zip", MAX_BYTES)
    payload = verify_download(plan, run, artifact, archive)

    # PR177's completion/readback ordering: requests succeeding is not evidence
    # of current effective state. A rerun or artifact replacement blocks install.
    observed_run = decode(api.get(run_endpoint))
    require(validate_run(plan, observed_run, run_id) == attempt, "run attempt changed; re-plan")
    observed_artifact = decode(api.get(artifact_endpoint))
    validate_artifact(plan, observed_run, {"total_count": 1, "artifacts": [observed_artifact]})
    for field in ("id", "digest", "size_in_bytes", "name"):
        require(observed_artifact.get(field) == artifact[field], "artifact changed during download")
    return install(plan, payload, artifact, run, destination)
