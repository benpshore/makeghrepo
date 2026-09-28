"""Golden snapshots: the exact rendered output of a fixed set of language combos.

The registry refactor (F2, #82) must reproduce these byte for byte. A change that
alters rendered output on purpose regenerates them with `uv run scripts/regen-golden`
and the diff gets reviewed. Never edit or hand-merge a `.golden` file.
"""

from __future__ import annotations

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
    raise NotImplementedError("TODO(opus): F1 #81")


def serialize(root: Path) -> str:
    """One deterministic text document describing every file under root.

    TODO(opus): F1 #81. Format, per file, sorted by POSIX relative path:
    a header line with the path, exec bit and byte length, then the exact
    content (base64 when not valid UTF-8), then an end marker.
    """
    raise NotImplementedError("TODO(opus): F1 #81")
