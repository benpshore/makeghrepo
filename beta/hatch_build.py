"""Bundle the shared implementation without depending on the stable distribution.

A checkout build reads ../src. An sdist carries the same sources in _shared;
building its wheel must work without the parent checkout or network access.
Neither artifact installs the stable makeghrepo console script.
"""

import json
from pathlib import Path, PurePosixPath

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        shared = root / "_shared" / "makeghrepo"
        if not shared.is_dir():
            shared = root.parent / "src" / "makeghrepo"
        if not (shared / "registry.py").is_file():
            raise RuntimeError("beta build needs the bundled makeghrepo sources")
        target = "makeghrepo" if self.target_name == "wheel" else "_shared/makeghrepo"
        manifest = json.loads((root / "shared-files.json").read_text())
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema") != 1
            or not isinstance(files, list)
            or not all(isinstance(name, str) for name in files)
            or "registry.py" not in files
            or len(set(files)) != len(files)
        ):
            raise RuntimeError("invalid beta shared-source manifest")
        # force_include bypasses normal build exclusions. Enumerating the local
        # directory would bundle untracked .env files, credentials or scratch
        # code. Include only this reviewed manifest, also shipped in the sdist.
        payload = {}
        for name in files:
            relative = PurePosixPath(name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or str(relative) != name
                or "\\" in name
                or "__pycache__" in relative.parts
                or relative.suffix in {".pyc", ".pyo"}
            ):
                raise RuntimeError("unsafe path in beta shared-source manifest")
            source = shared.joinpath(*relative.parts)
            path = shared
            for part in ("", *relative.parts):
                path = path / part
                if path.is_symlink():
                    raise RuntimeError(f"unexpected symlink in shared package: {relative}")
            if not source.is_file():
                raise RuntimeError(f"missing shared source: {relative}")
            payload[str(source)] = f"{target}/{relative}"
        build_data.setdefault("force_include", {}).update(payload)
