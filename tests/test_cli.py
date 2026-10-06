"""Tests for the CLI surface: --help visibility, --dry-run behavior, --json output shape."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "create_agent_apps.py"


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a subprocess. Returns CompletedProcess with .stdout/.stderr."""
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=full_env,
    )


# ---------------------------------------------------------------------------
# T1: --help
# ---------------------------------------------------------------------------


def test_help_shows_all_new_flags() -> None:
    """Every v0.2 flag is documented in --help output."""
    r = _run_cli("--help")
    assert r.returncode == 0
    for flag in ("--consumers", "--verify", "--resume", "--json", "--no-input"):
        assert flag in r.stdout, f"missing {flag} in --help"


def test_help_still_shows_original_flags() -> None:
    """Backward compat: pre-v0.2 flags still present."""
    r = _run_cli("--help")
    assert r.returncode == 0
    for flag in ("--org", "--agent", "--preset", "--format", "--output-dir", "--prefix", "--dry-run"):
        assert flag in r.stdout, f"missing pre-existing {flag} in --help"


def test_help_lists_supported_consumers() -> None:
    """--consumers help text names the supported harnesses."""
    r = _run_cli("--help")
    assert "hermes" in r.stdout


# ---------------------------------------------------------------------------
# T2: --dry-run without gh
# ---------------------------------------------------------------------------


def test_dry_run_does_not_require_gh(no_gh_path: None) -> None:
    """--dry-run should print the would-be command and exit 0 without invoking gh."""
    r = _run_cli("--org", "testorg", "--agent", "alpha", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "[dry-run] gh app-create create" in r.stdout


def test_dry_run_does_not_create_output_dir(no_gh_path: None, tmp_path: Path) -> None:
    """--dry-run shouldn't write any files to the output directory."""
    output_dir = tmp_path / "creds"
    r = _run_cli(
        "--org", "testorg", "--agent", "alpha",
        "--output-dir", str(output_dir),
        "--dry-run",
    )
    assert r.returncode == 0
    assert not output_dir.exists(), "dry-run should not have created output dir"


def test_dry_run_with_resume_is_clean_no_op(no_gh_path: None, tmp_path: Path) -> None:
    """--dry-run + --resume on a non-existent state file = 0 agents to do, rc=0."""
    r = _run_cli(
        "--org", "testorg", "--agent", "alpha",
        "--output-dir", str(tmp_path),
        "--dry-run", "--resume",
    )
    assert r.returncode == 0


# ---------------------------------------------------------------------------
# T3: --json + --resume
# ---------------------------------------------------------------------------


def test_json_with_resume_emits_clean_stdout(pre_completed_state: Path) -> None:
    """--json + --resume: [resume] chatter on stderr, JSON object on stdout."""
    r = _run_cli(
        "--org", "testorg", "--agent", "alpha",
        "--output-dir", str(pre_completed_state),
        "--resume", "--json", "--dry-run",
    )
    assert r.returncode == 0, f"stderr: {r.stderr}"

    # [resume] should NOT pollute stdout — it should be on stderr
    assert "[resume]" not in r.stdout, (
        f"[resume] leaked to stdout: {r.stdout!r}"
    )
    assert "[resume]" in r.stderr, "[resume] should be on stderr"

    # stdout should be only the JSON object
    parsed = json.loads(r.stdout.strip())
    assert "succeeded" in parsed
    assert "failed" in parsed
    assert "consumers" in parsed
    assert "verify" in parsed
    assert "resume" in parsed
    assert parsed["resume"] is True
    assert parsed["succeeded"] == []  # nothing pending
    assert parsed["failed"] == []


# ---------------------------------------------------------------------------
# T4: --json + --dry-run
# ---------------------------------------------------------------------------


def test_json_with_dry_run_succeeds(tmp_path: Path) -> None:
    """--json + --dry-run on a fresh dir: succeeded=[alpha], failed=[]."""
    r = _run_cli(
        "--org", "testorg", "--agent", "alpha",
        "--output-dir", str(tmp_path),
        "--dry-run", "--json",
    )
    assert r.returncode == 0
    parsed = json.loads(r.stdout.strip())
    assert parsed["succeeded"] == ["alpha"]
    assert parsed["failed"] == []


def test_json_structure_is_stable(tmp_path: Path) -> None:
    """JSON output has the same keys regardless of which other flags are set."""
    keys_seen: set[str] | None = None
    for extra in ([], ["--verify"], ["--no-input"], ["--resume"]):
        r = _run_cli(
            "--org", "testorg", "--agent", "alpha",
            "--output-dir", str(tmp_path), "--dry-run", "--json",
            *extra,
        )
        assert r.returncode == 0
        parsed = json.loads(r.stdout.strip())
        if keys_seen is None:
            keys_seen = set(parsed.keys())
        else:
            assert set(parsed.keys()) == keys_seen, f"keys differ with {extra}: {parsed.keys()}"


# ---------------------------------------------------------------------------
# T11: --consumers in --json
# ---------------------------------------------------------------------------


def test_consumers_appears_in_json_output(tmp_path: Path) -> None:
    """--consumers hermes is reflected in the JSON output as a list."""
    r = _run_cli(
        "--org", "testorg", "--agent", "alpha",
        "--output-dir", str(tmp_path),
        "--dry-run", "--consumers", "hermes", "--json",
    )
    assert r.returncode == 0
    parsed = json.loads(r.stdout.strip())
    assert parsed["consumers"] == ["hermes"]


def test_consumers_with_invalid_value_fails() -> None:
    """Unknown consumer name -> argparse error, non-zero exit."""
    r = _run_cli(
        "--org", "testorg", "--agent", "alpha",
        "--consumers", "not-a-real-consumer", "--dry-run",
    )
    assert r.returncode != 0
    assert "invalid choice" in r.stderr.lower() or "not-a-real-consumer" in r.stderr
