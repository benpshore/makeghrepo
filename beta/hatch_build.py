"""Bundle the shared implementation without depending on the stable distribution.

A checkout build reads ../src. An sdist carries the same sources in _shared;
building its wheel must work without the parent checkout or network access.
Neither artifact installs the stable makeghrepo console script.
"""

from pathlib import Path

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
        # Enumerate regular source files so local bytecode/cache files never leak
        # into a beta wheel, even when building after local development/tests.
        for source in sorted(shared.rglob("*")):
            relative = source.relative_to(shared)
            if "__pycache__" in relative.parts or source.suffix in {".pyc", ".pyo"}:
                continue
            if source.is_symlink():
                raise RuntimeError(f"unexpected symlink in shared package: {relative}")
            if source.is_file():
                build_data.setdefault("force_include", {})[str(source)] = f"{target}/{relative}"

