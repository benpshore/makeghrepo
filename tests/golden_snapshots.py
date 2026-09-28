"""Golden snapshots: the exact rendered output of a fixed set of language combos.

The registry refactor (F2, #82) must reproduce these byte for byte. A change that
alters rendered output on purpose regenerates them with `uv run scripts/regen-golden`
and the diff gets reviewed. Never edit or hand-merge a `.golden` file.
"""

from __future__ import annotations

import base64
import difflib
import stat
from pathlib import Path

from makeghrepo import scaffold

GOLDEN_DIR = Path(__file__).parent / "golden"

# Fixed inputs so rendering is deterministic; `year` matters most, since
# scaffold.render() otherwise defaults it to today's year.
DATA: dict[str, object] = {
    "project_name": "quiet-otter",
    "package_name": "quiet_otter",
    "description": "a test project",
    "author_name": "Test User",
    "github_owner": "someone",
    "year": "2026",
    "private": False,
}

_MULTI = [
    ["rust", "docker"],
    ["swift", "python"],
    ["js", "css", "api"],
    ["c", "cpp", "objc", "objcpp"],
    ["postgres", "sql", "shell"],
    # Every Dockerfile branch (python, rust, js, base) with the checks that share
    # the runner: these are where a lockfile/setup ordering race would show up.
    ["python", "docker"],
    ["js", "docker"],
    ["python", "rust", "docker"],
    ["go", "docker"],
    ["ts", "docker"],
    ["js", "ts", "css"],
]


def _combos() -> dict[str, dict[str, object]]:
    """Snapshot name -> copier data layered over DATA.

    Covers: no language, every token alone, the multi-language combos from
    test_template.COMBOS, every token together (in LANGUAGES order), and the
    private / python-library variants.
    """
    combos: dict[str, dict[str, object]] = {"base": {"languages": []}}
    for lang in scaffold.LANGUAGES:
        combos[lang] = {"languages": [lang]}
    for langs in _MULTI:
        combos["+".join(langs)] = {"languages": langs}
    combos["all"] = {"languages": list(scaffold.LANGUAGES)}
    combos["base-private"] = {"languages": [], "private": True}
    combos["python-private"] = {"languages": ["python"], "private": True}
    combos["all-private"] = {"languages": list(scaffold.LANGUAGES), "private": True}
    combos["python-lib"] = {"languages": ["python"], "py_lib": True}
    return combos


COMBOS = _combos()


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.golden"


def render_combo(name: str, dest: Path) -> Path:
    """Render COMBOS[name] (over DATA) into dest, which must not exist yet; return dest."""
    scaffold.render(dest, {**DATA, **COMBOS[name]})
    return dest


def serialize(root: Path) -> str:
    """One deterministic text document describing every file under root.

    Per file, sorted by POSIX relative path: a header line with the path, the
    owner-exec bit (full modes depend on umask), the exact byte length (so a
    trailing-newline change shows) and the encoding; then the content, as-is
    if UTF-8, else one line of base64; then an end marker.
    """
    parts: list[str] = []
    entries = [p for p in root.rglob("*") if p.is_symlink() or not p.is_dir()]
    for path in sorted(entries, key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"symlink in rendered output: {rel}")
        data = path.read_bytes()
        executable = int(bool(path.stat().st_mode & stat.S_IXUSR))
        try:
            text, encoding = data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            text, encoding = base64.b64encode(data).decode("ascii"), "base64"
        header = f"### FILE {rel} exec={executable} bytes={len(data)} encoding={encoding}"
        parts.append(f"{header}\n{text}\n### END\n")
    return "".join(parts)


def read_golden(name: str) -> str:
    # Bytes, not read_text(): universal-newline translation would hide a \r\n change.
    return golden_path(name).read_bytes().decode("utf-8")


def diff_report(name: str, expected: str, actual: str, limit: int = 80) -> str:
    lines = list(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"golden/{name}.golden",
            tofile="rendered",
        )
    )
    if len(lines) > limit:
        lines = [*lines[:limit], "... (truncated)\n"]
    return (
        f"rendered output for combo {name!r} differs from tests/golden/{name}.golden\n"
        + "".join(lines)
        + "\nIf this change is intended, run `uv run scripts/regen-golden` and review the diff."
    )


# The private variants run exactly the same checks as their public twins.
_MATRIX_SKIP = {"base-private", "python-private", "all-private"}
LINUX_RUNNER = "ubuntu-24.04"
MAC_RUNNER = "macos-latest"


def ci_matrix() -> dict[str, list[dict[str, str]]]:
    """GitHub Actions matrix for makeghrepo's `templates` job: one entry per combo.

    A combo that includes a macOS-only language (registry ``host_os``) runs on
    macOS. `all` runs twice: on macOS for everything, and on Linux for
    everything that can run there together.
    """
    include: list[dict[str, str]] = []
    for name, data in COMBOS.items():
        if name in _MATRIX_SKIP:
            continue
        langs = data["languages"]
        mac = any("Darwin" in scaffold.LANGS[lang].host_os for lang in langs)
        include.append({"combo": name, "os": MAC_RUNNER if mac else LINUX_RUNNER})
        if name == "all":
            include.append({"combo": name, "os": LINUX_RUNNER})
    return {"include": include}
