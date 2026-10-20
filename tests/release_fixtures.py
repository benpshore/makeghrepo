"""Exercise the actual release verifier with inert, transferable artifacts."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def write_fixtures(root, version, source, *, split=False):
    slots = {
        "python": [f"makeghrepo-{version}-py3-none-any.whl", f"makeghrepo-{version}.tar.gz"],
    }
    for slot, names in slots.items():
        directory = root / slot if split else root
        directory.mkdir(parents=True, exist_ok=True)
        assets = []
        for name in names:
            content = ("inert fixture: " + name + "\n").encode()
            (directory / name).write_bytes(content)
            assets.append(
                {"name": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            )
        (directory / f"{slot}.json").write_text(
            json.dumps({"version": version, "source": source, "assets": assets})
        )


def verify_transfer(downloaded, version, source):
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-build.yml").read_text())
    verifier = workflow["jobs"]["publish"]["steps"][-2]["run"]
    cases = (
        "valid",
        "missing",
        "corrupt",
        "wrong_source",
        "wrong_version",
        "unexpected_file",
        "traversal_name",
        "duplicate_asset",
        "symlink_payload",
        "invalid_source",
        "invalid_version",
    )
    for case in cases:
        with tempfile.TemporaryDirectory(prefix="release-transfer-") as temporary:
            trial = Path(temporary)
            dist = trial / "dist"
            shutil.copytree(downloaded, dist, symlinks=True)
            payload = dist / f"makeghrepo-{version}-py3-none-any.whl"
            inventory = dist / "python.json"
            data = json.loads(inventory.read_text())
            env = os.environ | {"VERSION": version, "SOURCE": source}
            if case == "missing":
                payload.unlink()
            elif case == "corrupt":
                payload.write_bytes(b"corrupt fixture")
            elif case == "wrong_source":
                data["source"] = "b" * 40
            elif case == "wrong_version":
                data["version"] = "99.0.0"
            elif case == "unexpected_file":
                (dist / "unexpected.txt").write_bytes(b"inert extra file")
            elif case == "traversal_name":
                data["assets"][0]["name"] = "../outside.whl"
            elif case == "duplicate_asset":
                data["assets"].append(data["assets"][0])
            elif case == "symlink_payload":
                target = trial / "inert-target"
                payload.rename(target)
                payload.symlink_to(target)
            elif case == "invalid_source":
                env["SOURCE"] = "invalid-source"
            elif case == "invalid_version":
                env["VERSION"] = "invalid-version"
            inventory.write_text(json.dumps(data))
            result = subprocess.run(
                ["bash", "-c", verifier], cwd=trial, env=env, capture_output=True
            )
            if case == "valid":
                assert result.returncode == 0, result.stderr.decode()
                assets = json.loads((trial / "expected-assets.json").read_text())
                assert len(assets) == 2
                assert {p.name for p in (trial / "payload").iterdir()} == {
                    a["name"] for a in assets
                }
            else:
                assert result.returncode != 0, f"accepted {case}"
                assert not (trial / "expected-assets.json").exists(), case
    return len(cases) - 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("create", "verify"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    version, source = "0.0.0", "a" * 40
    if args.operation == "create":
        write_fixtures(args.directory, version, source, split=True)
        print("Created one inert release artifact containing two payloads")
    else:
        rejected = verify_transfer(args.directory, version, source)
        print(f"Verified two downloaded payloads; rejected {rejected} invalid transfers")
