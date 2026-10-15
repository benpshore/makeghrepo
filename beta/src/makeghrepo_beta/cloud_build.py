"""Build the canonical starter in the existing Actions CI, never in the client."""

from __future__ import annotations

import argparse
import os
import platform
import stat
import subprocess
import sys
import tempfile
import zipfile
from importlib.resources import files
from pathlib import Path

from makeghrepo_beta import seedlock
from makeghrepo_beta.cloud_contract import (
    CHECKS,
    PACKAGE,
    PROFILE,
    REPOSITORY,
    WHEEL,
    WORKFLOW,
    CloudPlan,
    artifact_name,
    canonical,
    decode,
    integer,
    inventory,
    require,
    sha256,
    validate_bundle,
)

BUILD_REQUIREMENTS = tuple(PROFILE["build_requirements"])


def packaged_lock() -> bytes:
    template = (files("makeghrepo_beta") / "assets/python-uv.lock.jinja").read_bytes()
    contract = decode((files("makeghrepo_beta") / "assets/python-seed.json").read_bytes())
    require(sha256(template) == contract["provenance"]["template_sha256"], "seed template drift")
    text = template.decode()
    require(text.count("[[ project_name | to_json ]]") == 1, "unexpected seed substitution")
    text = text.replace("[% raw %]", "").replace("[% endraw %]", "")
    text = text.replace("[[ project_name | to_json ]]", f'"{PACKAGE}"')
    require("[%" not in text and "project_name" not in text, "unexpanded seed template")
    return text.encode()


def environment(root: Path) -> dict[str, str]:
    """A fresh cloud environment with no GitHub/SSH tokens or user uv overrides."""
    (root / "home").mkdir()
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(root / "home"),
        "TMPDIR": str(root),
        "LANG": "C.UTF-8",
        "UV_CACHE_DIR": str(root / "cache"),
        "UV_PROJECT_ENVIRONMENT": str(root / "project-env"),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_CONFIG": "1",
        "UV_NO_ENV_FILE": "1",
        "UV_DEFAULT_INDEX": "https://pypi.org/simple",
    }


def run(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError(f"cloud build failed: {command[0:3]}\n{result.stdout[-4000:]}{result.stderr[-4000:]}")
    return result.stdout.strip()


def write_bundle(path: Path, payload: dict, plan: CloudPlan, run_id: int, attempt: int) -> None:
    result = {
        "schema": 1, "asset": plan.asset(), "asset_key": plan.asset_key,
        "run_id": run_id, "run_attempt": attempt,
        "checks": {check: "passed" for check in CHECKS}, "files": inventory(payload),
    }
    contents = {**payload, "result.json": (canonical(result), 0o644)}
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, (content, mode) in sorted(contents.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(entry, content)
    validate_bundle(plan, path.read_bytes(), run_id, attempt)


def build(plan: CloudPlan, destination: Path, run_id: int, attempt: int) -> None:
    require(platform.system() == "Linux" and platform.machine() == "x86_64", "wrong cloud target")
    require(platform.python_version() == PROFILE["python"], "wrong cloud Python toolchain")
    from makeghrepo import scaffold

    with tempfile.TemporaryDirectory(prefix="makeghrepo-prebuild-") as temporary:
        root = Path(temporary)
        env = environment(root)
        require(run(["uv", "--version"], root, env).split()[1] == PROFILE["uv"], "wrong uv toolchain")
        source = root / "source"
        scaffold.render(source, {
            "project_name": PACKAGE, "package_name": "makeghrepo_seed",
            "github_owner": "makeghrepo-assets", "author_name": "makeghrepo",
            "description": "Prebuilt Python starter", "year": "2026",
            "private": False, "py_lib": False, "project_license": "none", "languages": ["python"],
        })
        (source / "uv.lock").write_bytes(packaged_lock())
        seedlock.validate_bootstrap(source, ["python"], PACKAGE, lib=False)
        # Capture the rendered allowlist BEFORE tools create environments/caches.
        payload = {}
        for path in sorted(source.rglob("*")):
            require(not path.is_symlink(), "renderer produced a symlink")
            if path.is_file():
                mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                payload[f"source/{path.relative_to(source).as_posix()}"] = (path.read_bytes(), mode)
        python = sys.executable
        run(["uv", "lock", "--check", "--offline", "--python", python], source, env)
        run(["uv", "sync", "--locked", "--no-install-project", "--python", python], source, env)
        run(["uv", "run", "--no-sync", "ruff", "format", "--check"], source, env)
        run(["uv", "run", "--no-sync", "ruff", "check"], source, env)
        run(["uv", "run", "--no-sync", "pytest"], source, {**env, "PYTHONPATH": str(source / "src")})
        run([python, "-m", "compileall", "-q", str(source / "src")], root, env)

        # Resolve build-tool dependencies once in makeghrepo CI, save the full
        # hashed closure, then build offline. Nothing is resolved by the client.
        requirements = root / "build.in"
        requirements.write_text("\n".join(BUILD_REQUIREMENTS) + "\n")
        locked = root / "build-requirements.lock"
        run(["uv", "pip", "compile", "--python", python, "--generate-hashes", "--no-header",
             "--no-annotate", "--only-binary", ":all:", "--output-file", str(locked), str(requirements)], root, env)
        builder = root / "builder"
        run(["uv", "venv", "--no-project", "--python", python, str(builder)], root, env)
        builder_python = str(builder / "bin/python")
        run(["uv", "pip", "sync", "--python", builder_python, "--require-hashes",
             "--only-binary", ":all:", str(locked)], root, env)
        dist = root / "dist"
        run(["uv", "build", str(source), "--wheel", "--python", builder_python,
             "--no-build-isolation", "--offline", "--out-dir", str(dist)], root, env)
        wheel = root / WHEEL
        require(wheel.is_file() and list(dist.glob("*.whl")) == [wheel], "unexpected wheel identity")
        installed = root / "installed"
        run(["uv", "venv", "--no-project", "--python", python, str(installed)], root, env)
        run(["uv", "pip", "install", "--python", str(installed / "bin/python"),
             "--offline", "--no-deps", str(wheel)], root, env)
        hello = run([str(installed / "bin/makeghrepo-seed")], root, env)
        require(hello == "Hello from makeghrepo-seed!", "installed wheel smoke test failed")
        for name, (content, _mode) in payload.items():
            require((root / name).read_bytes() == content, f"validated source changed: {name}")
        payload[WHEEL] = (wheel.read_bytes(), 0o644)
        payload["build/requirements.lock"] = (locked.read_bytes(), 0o644)
        destination.mkdir(parents=True, exist_ok=False)
        write_bundle(destination / "bundle.zip", payload, plan, run_id, attempt)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    require(os.environ.get("GITHUB_ACTIONS") == "true", "prebuilding is restricted to Actions CI")
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "unexpected builder repository")
    require(os.environ.get("GITHUB_WORKFLOW_REF", "").startswith(f"{REPOSITORY}/{WORKFLOW}@"),
            "unexpected builder workflow")
    plan = CloudPlan(os.environ["GITHUB_SHA"], "makeghrepo-assets", PACKAGE)
    run_id = integer(int(os.environ["GITHUB_RUN_ID"]), "run id")
    attempt = integer(int(os.environ["GITHUB_RUN_ATTEMPT"]), "run attempt")
    build(plan, args.out, run_id, attempt)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"artifact_name={artifact_name(plan, attempt)}\n")
    print(f"prebuilt and validated {plan.asset_key}")


if __name__ == "__main__":
    main()
