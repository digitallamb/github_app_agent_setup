"""Tests for the --install flag and _install_helper_script() function.

The install flag copies skill/github-app-git-auth/scripts/github_auth.py
from the repo into ~/.hermes/profiles/<agent>/scripts/ so the agent can
use the helper for git operations.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent  # tests/test_install.py -> tests -> <repo>
SCRIPT = REPO_ROOT / "create_agent_apps.py"
SKILL_HELPER = REPO_ROOT / "skill" / "github-app-git-auth" / "scripts" / "github_auth.py"


@pytest.fixture(scope="module")
def caas_module():
    """Import the create_agent_apps module so we can test internal functions."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("caas_install", str(SCRIPT))
    assert spec is not None, f"could not load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["caas_install"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# Direct function tests (no CLI invocation)
# ---------------------------------------------------------------------------


def test_install_helper_script_drops_file_in_agent_dir(
    caas_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When called, the helper is copied into the agent's scripts/ dir."""
    # Use the real function (writes to real ~/.hermes), but with a tmp
    # agent name that we can verify and clean up.
    test_agent = f"test-install-{os.getpid()}"
    try:
        ok, msg = caas_module._install_helper_script(test_agent, repo_root=REPO_ROOT)
        assert ok, msg
        dest = Path(f"/home/hermes/.hermes/profiles/{test_agent}/scripts/github_auth.py")
        assert dest.is_file()
        # Content matches the skill helper
        assert dest.read_text(encoding="utf-8") == SKILL_HELPER.read_text(encoding="utf-8")
        # Mode is 0755 (executable for the CLI form)
        assert oct(dest.stat().st_mode & 0o777) == "0o755"
    finally:
        # Clean up: remove the test agent's scripts dir
        test_dir = Path(f"/home/hermes/.hermes/profiles/{test_agent}")
        if test_dir.exists():
            shutil.rmtree(test_dir)


def test_install_helper_script_creates_scripts_dir_if_missing(
    caas_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ~/.hermes/profiles/<agent>/scripts/ doesn't exist, create it."""
    test_agent = f"test-install-mkdir-{os.getpid()}"
    test_dir = Path(f"/home/hermes/.hermes/profiles/{test_agent}")
    assert not test_dir.exists()
    try:
        ok, msg = caas_module._install_helper_script(test_agent, repo_root=REPO_ROOT)
        assert ok, msg
        assert (test_dir / "scripts" / "github_auth.py").is_file()
    finally:
        if test_dir.exists():
            import shutil
            shutil.rmtree(test_dir)


def test_install_helper_script_fails_gracefully_if_skill_missing(
    caas_module, tmp_path: Path
) -> None:
    """If the skill folder isn't shipped (e.g. partial clone), return (False, msg)."""
    # Pass a repo_root that doesn't have the skill folder
    fake_repo = tmp_path / "no-skill-repo"
    fake_repo.mkdir()
    ok, msg = caas_module._install_helper_script("any-agent", repo_root=fake_repo)
    assert ok is False
    assert "not found" in msg.lower() or "skill folder" in msg.lower()


# ---------------------------------------------------------------------------
# CLI-level tests
# ---------------------------------------------------------------------------


def test_help_shows_install_flag() -> None:
    """--install is in --help output."""
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "--install" in r.stdout


def test_install_flag_in_json_output(tmp_path: Path) -> None:
    """--install appears as a field in the JSON output."""
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--org", "testorg", "--agent", "savant-installer-test",
         "--output-dir", str(tmp_path),
         "--dry-run", "--json", "--install"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    parsed = json.loads(r.stdout.strip())
    assert "install" in parsed
    assert parsed["install"] is True  # --install was passed


def test_install_is_implied_with_hermes_and_verify(tmp_path: Path) -> None:
    """--verify + --consumers hermes implies --install."""
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--org", "testorg", "--agent", "savant-implied-test",
         "--output-dir", str(tmp_path),
         "--dry-run", "--json",
         "--consumers", "hermes", "--verify"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    parsed = json.loads(r.stdout.strip())
    assert "install" in parsed
    assert parsed["install"] is True  # implied, not explicit


def test_install_does_not_run_in_dry_run(tmp_path: Path) -> None:
    """--install + --dry-run does not touch the filesystem."""
    test_agent = "dry-run-test"
    test_dir = Path(f"/home/hermes/.hermes/profiles/{test_agent}")
    # Make sure it doesn't exist
    if test_dir.exists():
        import shutil
        shutil.rmtree(test_dir)
    try:
        r = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--org", "testorg", "--agent", test_agent,
             "--output-dir", str(tmp_path),
             "--dry-run", "--install", "--json"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        # Dry run: no install should have happened
        assert not test_dir.exists(), "dry-run should not have created the agent's profile dir"
    finally:
        if test_dir.exists():
            import shutil
            shutil.rmtree(test_dir)


# ---------------------------------------------------------------------------
# Helper import: a clean import test
# ---------------------------------------------------------------------------


def test_helper_script_is_self_contained() -> None:
    """The helper imports cleanly with only PyJWT as an external dep."""
    import subprocess
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '" + str(SKILL_HELPER.parent) + "'); "
         "import github_auth; "
         "assert hasattr(github_auth, 'mint_installation_token'); "
         "assert hasattr(github_auth, 'verify_github_app'); "
         "assert hasattr(github_auth, 'git_as_agent'); "
         "print('imports ok')"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"import failed: {r.stderr}"
    assert "imports ok" in r.stdout


def test_helper_script_has_cli_entry_point() -> None:
    """The helper has a __main__ block that supports the CLI form."""
    text = SKILL_HELPER.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in text
    assert "sys.argv" in text
    assert "--verify" in text
