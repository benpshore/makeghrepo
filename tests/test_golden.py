"""Golden snapshots: each combo's rendered output, byte for byte, plus its exec bits.

Every combo renders through the real ``scaffold.render`` with fixed data (``DATA``) and
is compared with ``tests/golden/<id>/``:

- ``tree/`` is the exact rendered project.
- ``manifest.txt`` lists every rendered path with a git-style mode: ``100644``,
  ``100755`` (executable), or ``040000`` for an empty directory (git can't store one
  in ``tree/``). It's the source of truth for which files exist and which are
  executable, independent of the checkout's file modes.

Any change to rendered output fails here. If the change is intended, run
``scripts/regen-golden`` (never edit or merge golden files by hand) and review the diff.
"""

from __future__ import annotations

import difflib
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from makeghrepo import scaffold

GOLDEN = Path(__file__).parent / "golden"
TEMPLATES = Path(scaffold.__file__).parent / "templates" / "project"
REGEN = Path(__file__).parent.parent / "scripts" / "regen-golden"

# The keys cli.main passes to scaffold.render, with fixed values. `year` is pinned so
# snapshots don't drift on Jan 1, and deliberately isn't the current year, so a render
# that ignored the year it was given would show up.
DATA: dict[str, object] = {
    "project_name": "quiet-otter",
    "package_name": "quiet_otter",
    "description": "quiet-otter",
    "author_name": "Test User",
    "github_owner": "someone",
    "private": False,
    "py_lib": False,
    "year": "2000",
}

# Multi-token combos, copied from test_template.py's COMBOS when F1 landed. Kept here as
# literals so later rewrites of that file can't move the golden set.
MULTI = [["rust", "docker"], ["swift", "python"], ["js", "css", "api"],
         ["c", "cpp", "objc", "objcpp"], ["postgres", "sql", "shell"]]  # fmt: skip

# (id, languages, extra data). Single tokens and `all` follow scaffold.LANGUAGES, so a new
# token gets its own snapshot and `all` grows with it (regenerate `all` on rebase).
COMBOS: list[tuple[str, list[str], dict[str, object]]] = [
    ("none", [], {}),
    *((lang, [lang], {}) for lang in scaffold.LANGUAGES),
    *(("+".join(langs), langs, {}) for langs in MULTI),
    ("all", list(scaffold.LANGUAGES), {}),
    ("all-private", list(scaffold.LANGUAGES), {"private": True}),
    ("python-lib", ["python"], {"py_lib": True}),
]
BY_ID = {cid: (langs, extra) for cid, langs, extra in COMBOS}

Snapshot = dict[str, tuple[str, bytes]]  # posix path -> (git mode, bytes; b"" for a dir)


def render(dest: Path, cid: str) -> Snapshot:
    langs, extra = BY_ID[cid]
    scaffold.render(dest, {**DATA, "languages": list(langs), **extra})
    return snapshot(dest)


def snapshot(root: Path) -> Snapshot:
    snap: Snapshot = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise AssertionError(f"{rel}: the golden format has no symlinks; extend it first")
        if path.is_dir():
            if not any(path.iterdir()):
                snap[rel] = ("040000", b"")
        else:
            mode = "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"
            snap[rel] = (mode, path.read_bytes())
    return snap


def manifest(snap: Snapshot) -> str:
    return "".join(f"{mode} {rel}\n" for rel, (mode, _) in sorted(snap.items()))


def write_golden(cid: str, snap: Snapshot) -> None:
    base = GOLDEN / cid
    if base.exists():
        shutil.rmtree(base)
    tree = base / "tree"
    tree.mkdir(parents=True)
    for rel, (mode, data) in snap.items():
        path = tree / rel
        if mode == "040000":
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if mode == "100755":
            path.chmod(path.stat().st_mode | 0o111)
    (base / "manifest.txt").write_text(manifest(snap))


def mismatches(cid: str, snap: Snapshot) -> list[str]:
    """Every way `snap` differs from tests/golden/<cid>/. Empty means identical."""
    base = GOLDEN / cid
    if not (base / "manifest.txt").is_file():
        return [f"no golden snapshot at tests/golden/{cid}/"]
    want: dict[str, str] = {}
    for line in (base / "manifest.txt").read_text().splitlines():
        mode, rel = line.split(" ", 1)
        want[rel] = mode
    got = {rel: mode for rel, (mode, _) in snap.items()}
    tree = base / "tree"
    on_disk = {p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file()}
    want_files = {rel for rel, mode in want.items() if mode != "040000"}

    problems = [f"missing (in golden, not rendered): {r}" for r in sorted(want.keys() - got)]
    problems += [f"extra (rendered, not in golden): {r}" for r in sorted(got.keys() - want)]
    problems += [
        f"mode {want[r]} -> {got[r]}: {r}" for r in sorted(want.keys() & got) if want[r] != got[r]
    ]
    problems += [
        f"golden tree lacks a manifest file (not committed? gitignored?): {r}"
        for r in sorted(want_files - on_disk)
    ]
    problems += [f"golden tree has an unlisted file: {r}" for r in sorted(on_disk - want_files)]
    for rel in sorted(want_files & on_disk & got.keys()):
        old, new = (tree / rel).read_bytes(), snap[rel][1]
        if old != new:
            problems.append(f"content: {rel}\n{_diff(rel, old, new)}")
    return problems


def _diff(rel: str, old: bytes, new: bytes, limit: int = 30) -> str:
    try:
        a, b = old.decode().splitlines(keepends=True), new.decode().splitlines(keepends=True)
    except UnicodeDecodeError:
        return f"  (binary: {len(old)} -> {len(new)} bytes)"
    lines = [line.rstrip("\n") for line in difflib.unified_diff(a, b, f"golden/{rel}", rel)]
    more = [f"  ... {len(lines) - limit} more diff lines"] if len(lines) > limit else []
    return "\n".join([f"  {line!r}" for line in lines[:limit]] + more)


def report(cid: str, problems: list[str]) -> str:
    return (
        f"rendered output for {cid!r} differs from tests/golden/{cid}/:\n"
        + "\n".join(problems)
        + "\nIf this change is intended, run scripts/regen-golden and review the diff."
    )


@pytest.fixture(scope="session")
def update(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture(scope="session")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], Snapshot]:
    """Render each combo from the package's own templates, at most once per session."""
    cache: dict[str, Snapshot] = {}

    def get(cid: str) -> Snapshot:
        if cid not in cache:
            cache[cid] = render(tmp_path_factory.mktemp(cid) / "quiet-otter", cid)
        return cache[cid]

    return get


@pytest.mark.parametrize("cid", list(BY_ID))
def test_rendered_output_matches_golden(cid: str, rendered, update: bool) -> None:
    snap = rendered(cid)
    if update:
        write_golden(cid, snap)
    problems = mismatches(cid, snap)
    assert not problems, report(cid, problems)


def test_golden_holds_exactly_the_combos(update: bool) -> None:
    stale = {p.name for p in GOLDEN.iterdir()} - set(BY_ID) if GOLDEN.is_dir() else set()
    if update:
        for name in stale:
            path = GOLDEN / name
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        stale = set()
    assert not stale, f"not a combo in test_golden.py: {sorted(stale)}; run scripts/regen-golden"


def test_combos_are_unique_and_known() -> None:
    ids = [cid for cid, _, _ in COMBOS]
    assert len(ids) == len(set(ids))
    for _, langs, _ in COMBOS:
        assert set(langs) <= set(scaffold.LANGUAGES)


def test_render_is_deterministic(rendered, tmp_path: Path) -> None:
    assert render(tmp_path / "quiet-otter", "all") == rendered("all")


def test_regen_script_is_executable() -> None:
    assert REGEN.is_file()
    assert REGEN.stat().st_mode & stat.S_IXUSR


# --- The check itself catches template changes -------------------------------------------
# These mutate a private copy of the templates and render it through scaffold.render.


@pytest.fixture
def templates(tmp_path: Path, update: bool) -> Path:
    if update:
        pytest.skip("checks the golden check itself; nothing to update")
    copy = tmp_path / "pkg" / "templates" / "project"
    shutil.copytree(TEMPLATES, copy)
    return copy


def render_from(templates: Path, dest: Path, cid: str, monkeypatch: pytest.MonkeyPatch) -> Snapshot:
    with monkeypatch.context() as m:
        m.setattr(scaffold, "files", lambda _pkg: templates.parent.parent)
        return render(dest, cid)


def flip_one_byte(template: Path, output: bytes) -> None:
    """Change one byte of `template`, inside a line that's copied verbatim into `output`."""
    old = template.read_bytes()
    lines = old.split(b"\n")
    out_lines = set(output.split(b"\n"))
    for i, line in enumerate(lines):
        literal = not any(tag in line for tag in (b"[[", b"[%", b"[#"))
        if literal and len(line.strip()) >= 8 and line in out_lines:
            j = next(j for j, c in enumerate(line) if chr(c).isalpha())
            lines[i] = line[:j] + line[j : j + 1].swapcase() + line[j + 1 :]
            break
    else:
        raise AssertionError(f"{template.name}: no verbatim line to change")
    new = b"\n".join(lines)
    assert len(new) == len(old) and sum(a != b for a, b in zip(old, new, strict=True)) == 1
    template.write_bytes(new)


@pytest.mark.parametrize(
    ("template", "cid", "outputs"),
    [
        ("template/SECURITY.md", "none", ["SECURITY.md"]),  # copied as-is
        ("template/[% if rs %]Cargo.toml[% endif %].jinja", "rust", ["Cargo.toml"]),  # rendered
        ("_checks.jinja", "python", ["AGENTS.md", "README.md"]),  # included by both
    ],
)
def test_a_one_byte_template_change_fails_the_check(
    templates, rendered, tmp_path, monkeypatch, template, cid, outputs
) -> None:
    assert mismatches(cid, rendered(cid)) == []  # otherwise the check below proves nothing
    flip_one_byte(templates / template, rendered(cid)[outputs[0]][1])
    problems = mismatches(cid, render_from(templates, tmp_path / "out", cid, monkeypatch))
    assert [p.split("\n")[0] for p in problems] == [f"content: {out}" for out in outputs]


def test_an_exec_bit_change_fails_the_check(templates, rendered, tmp_path, monkeypatch) -> None:
    assert mismatches("shell", rendered("shell")) == []
    script = templates / "template/[% if sh %]scripts[% endif %]/hello.sh.jinja"
    script.chmod(script.stat().st_mode & ~0o111)
    problems = mismatches("shell", render_from(templates, tmp_path / "out", "shell", monkeypatch))
    assert problems == ["mode 100755 -> 100644: scripts/hello.sh"]


def test_an_added_or_removed_file_fails_the_check(templates, rendered, tmp_path, monkeypatch):
    assert mismatches("none", rendered("none")) == []
    (templates / "template/NEW.md").write_text("new\n")
    (templates / "template/SECURITY.md").unlink()
    problems = mismatches("none", render_from(templates, tmp_path / "out", "none", monkeypatch))
    assert problems == [
        "missing (in golden, not rendered): SECURITY.md",
        "extra (rendered, not in golden): NEW.md",
    ]


def test_every_template_file_reaches_some_golden(templates, tmp_path, monkeypatch) -> None:
    """A template file that no combo renders is one the golden check can't guard."""
    sources = sorted(p for p in templates.rglob("*") if p.is_file() and p.name != "copier.yml")
    markers: dict[bytes, str] = {}
    for i, path in enumerate(sources):
        marker = f"<golden-reach-{i:03d}>".encode()
        path.write_bytes(path.read_bytes() + b"\n" + marker + b"\n")
        markers[marker] = path.relative_to(templates).as_posix()
    unseen = set(markers)
    for cid in ["all", *(c for c in BY_ID if c != "all")]:  # `all` covers nearly everything
        if not unseen:
            break
        out = render_from(templates, tmp_path / cid, cid, monkeypatch)
        blob = b"".join(data for _, data in out.values())
        unseen = {m for m in unseen if m not in blob}
    assert not unseen, f"never rendered by any golden combo: {sorted(markers[m] for m in unseen)}"
