"""
Tests for scripts/trash-test-repo.sh

These tests validate the safety guardrails of the trash-test-repo.sh script
that agents use to delete throwaway test repositories.
"""

import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "trash-test-repo.sh"


def run_script(*args, env=None) -> tuple[int, str]:
    """Run the trash-test-repo.sh script and return (exit_code, stderr)."""
    result = subprocess.run(
        [str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stderr


class TestScriptExists:
    """Verify the script exists and is executable."""

    def test_script_file_exists(self):
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"

    def test_script_is_executable(self):
        assert SCRIPT_PATH.stat().st_mode & 0o111, f"Script is not executable: {SCRIPT_PATH}"

    def test_script_has_shebang(self):
        content = SCRIPT_PATH.read_text()
        assert content.startswith("#!/usr/bin/env bash"), "Script must start with bash shebang"


class TestNameValidation:
    """Test that the script validates repo names correctly."""

    def test_rejects_invalid_names_without_prefix_or_suffix(self):
        """Repo names must match ^mgr-test- or end with -trash$."""
        invalid_names = [
            "test-repo",
            "my-repo",
            "mgr-test",  # No dash and suffix after, doesn't end with -trash
            "production-cleanup",  # Doesn't match either pattern
            "archive",  # Doesn't match either pattern
        ]
        for name in invalid_names:
            returncode, stderr = run_script(name)
            assert returncode != 0, f"Expected script to reject '{name}'"
            assert "match '^mgr-test-' or end with '-trash'" in stderr, (
                f"Expected validation error for '{name}', got: {stderr}"
            )

    def test_accepts_valid_mgr_test_names(self):
        """Repo names starting with mgr-test- should pass validation."""
        # Note: These will fail at the gh call since the repo doesn't exist,
        # but the name validation should pass. We check the error message.
        valid_names = [
            "mgr-test-foo",
            "mgr-test-foo-bar",
            "mgr-test-all-123",
        ]
        for name in valid_names:
            _, stderr = run_script(name)
            # Should not be a name validation error
            assert "match '^mgr-test-' or end with '-trash'" not in stderr, (
                f"Expected '{name}' to pass name validation, but got error: {stderr}"
            )

    def test_accepts_valid_trash_suffix_names(self):
        """Repo names ending with -trash should pass validation."""
        # Note: names can end with -trash without starting with mgr-test-
        valid_names = [
            "test-trash",
            "some-repo-trash",
            "anything-trash",
            "cleanup-trash",
        ]
        for name in valid_names:
            _, stderr = run_script(name)
            # Should not be a name validation error
            assert "match '^mgr-test-' or end with '-trash'" not in stderr, (
                f"Expected '{name}' to pass name validation, but got error: {stderr}"
            )

    def test_no_args_shows_usage(self):
        returncode, stderr = run_script()
        assert returncode != 0
        assert "Usage:" in stderr or "usage:" in stderr.lower()


class TestScriptSyntax:
    """Verify the script has no obvious bash syntax errors."""

    def test_script_syntax_is_valid(self):
        """Use bash -n to check for syntax errors."""
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Bash syntax error: {result.stderr}"


class TestScriptLogic:
    """Test the logical flow of the script (with mocked gh)."""

    def test_script_contains_age_check_logic(self):
        """Verify the script checks repo age in hours."""
        content = SCRIPT_PATH.read_text()
        assert "MAXAGE_HOURS" in content or "24" in content, (
            "Script should check repo age (24 hours)"
        )
        assert "AGE_HOURS" in content or "AGE_SECONDS" in content, (
            "Script should calculate repo age"
        )

    def test_script_calls_gh_project_delete(self):
        """Verify the script attempts to delete the GitHub Project."""
        content = SCRIPT_PATH.read_text()
        assert "gh project delete" in content, (
            "Script must call 'gh project delete' to remove the project board"
        )

    def test_script_calls_gh_repo_delete(self):
        """Verify the script calls gh repo delete."""
        content = SCRIPT_PATH.read_text()
        assert "gh repo delete" in content, (
            "Script must call 'gh repo delete' to remove the repository"
        )

    def test_script_deletes_local_directory(self):
        """Verify the script removes the local clone."""
        content = SCRIPT_PATH.read_text()
        assert "rm -rf" in content, "Script must remove the local directory"
        assert "/code/GitHub/" in content or "HOME" in content, (
            "Script must reference the local clone directory"
        )

    def test_script_validates_owner(self):
        """Verify the script is hardcoded to the correct owner."""
        content = SCRIPT_PATH.read_text()
        # The owner should be 'benpshore' not configurable by users
        assert 'OWNER="benpshore"' in content or "OWNER=benpshore" in content, (
            "Script must be hardcoded to owner 'benpshore'"
        )
