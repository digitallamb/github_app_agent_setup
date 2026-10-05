"""Tests for the hermes consumer adapter.

The hermes adapter adapts gh-app-create's inline-PEM output to the format
Hermes's _try_github_app() expects: a path to a 0600 PEM file, not the
inline value. It also knows how to read the post-install URL and verify
the App is usable end-to-end.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# T5: hermes adapter extracts PEM, writes path, strips inline
# ---------------------------------------------------------------------------


def test_hermes_adapt_extracts_pem(caas, fake_env_file: Path, tmp_path: Path) -> None:
    """After adapt, the inline PEM is replaced with GITHUB_APP_PRIVATE_KEY_PATH=...,
    and a 0600 PEM file is written next to the env file."""
    original = fake_env_file.read_text(encoding="utf-8")
    adapted = caas._hermes_adapt(original, "testagent", tmp_path)

    # Path key is present
    assert "GITHUB_APP_PRIVATE_KEY_PATH=" in adapted

    # Inline PEM is gone
    assert "GITHUB_APP_PRIVATE_KEY='" not in adapted

    # Other keys are preserved
    assert "GITHUB_APP_ID=12345" in adapted
    assert "GITHUB_APP_SLUG=test-agent" in adapted
    assert "GITHUB_APP_CLIENT_ID=Iv23abc" in adapted
    assert "GITHUB_APP_CLIENT_SECRET=secret123" in adapted
    assert "GITHUB_APP_WEBHOOK_SECRET=hooksecret456" in adapted

    # PEM file was created with correct content and mode
    pem = tmp_path / "testagent.pem"
    assert pem.is_file()
    assert oct(pem.stat().st_mode & 0o777) == "0o600"
    pem_contents = pem.read_text(encoding="utf-8")
    assert "-----BEGIN RSA PRIVATE KEY-----" in pem_contents
    assert "MIIEowIBAAKCAQEAtest" in pem_contents
    assert pem_contents.rstrip().endswith("-----END RSA PRIVATE KEY-----")


def test_hermes_adapt_writes_pem_to_expected_path(caas, fake_env_file: Path, tmp_path: Path) -> None:
    """The PEM file is at <output_dir>/<full_name>.pem, not somewhere else."""
    adapted = caas._hermes_adapt(fake_env_file.read_text(encoding="utf-8"), "testagent", tmp_path)
    # Extract the path from the env text
    for line in adapted.splitlines():
        if line.startswith("GITHUB_APP_PRIVATE_KEY_PATH="):
            pem_path = Path(line.split("=", 1)[1].strip())
            assert pem_path == tmp_path / "testagent.pem"
            assert pem_path.is_file()
            break
    else:
        pytest.fail("GITHUB_APP_PRIVATE_KEY_PATH= line not found in adapted env")


# ---------------------------------------------------------------------------
# T6: idempotence
# ---------------------------------------------------------------------------


def test_hermes_adapt_is_idempotent(caas, fake_env_file: Path, tmp_path: Path) -> None:
    """Running the adapter twice produces identical output. Re-running on
    already-adapted env is a no-op (doesn't re-extract the path or write
    a new file)."""
    once = caas._hermes_adapt(fake_env_file.read_text(encoding="utf-8"), "testagent", tmp_path)
    twice = caas._hermes_adapt(once, "testagent", tmp_path)
    assert once == twice, "adapter is not idempotent"


def test_hermes_adapt_on_already_adapted_env_creates_one_pem(
    caas, fake_env_file: Path, tmp_path: Path
) -> None:
    """Re-running adapt on already-adapted env doesn't write a duplicate .pem file."""
    once = caas._hermes_adapt(fake_env_file.read_text(encoding="utf-8"), "testagent", tmp_path)
    pems_before = list(tmp_path.glob("*.pem"))
    twice = caas._hermes_adapt(once, "testagent", tmp_path)
    pems_after = list(tmp_path.glob("*.pem"))
    assert pems_before == pems_after, f"PEM files changed: {pems_before} -> {pems_after}"


# ---------------------------------------------------------------------------
# T12: realistic env
# ---------------------------------------------------------------------------


def test_hermes_adapt_handles_realistic_env(caas, realistic_env_file: Path, tmp_path: Path) -> None:
    """The user's real-world env (csenteninels-savant) is adapted correctly.

    The slug, ID, client IDs, and other values pass through verbatim.
    Only the inline PEM is replaced with a path reference.
    """
    adapted = caas._hermes_adapt(
        realistic_env_file.read_text(encoding="utf-8"),
        "csenteninels-savant",
        tmp_path,
    )
    # Real values preserved
    assert "GITHUB_APP_ID=5183131" in adapted
    assert "GITHUB_APP_SLUG=csenteninels-savant" in adapted
    assert "GITHUB_APP_CLIENT_ID=Iv23liBfkFDRQ3gbhKZ0" in adapted
    assert "GITHUB_APP_CLIENT_SECRET=9b181eb83cfae29b4cc758ff7f768abf31f910db" in adapted
    assert "GITHUB_APP_WEBHOOK_SECRET=39724e5c10c89cc13c41282f1ea6704397386649" in adapted
    # PEM was extracted
    pem = tmp_path / "csenteninels-savant.pem"
    assert pem.is_file()
    # Path reference is present and points to the right file
    assert f"GITHUB_APP_PRIVATE_KEY_PATH={pem}" in adapted
    # INSTALLATION_ID is NOT added by adapt — only post_install adds it
    assert "GITHUB_APP_INSTALLATION_ID" not in adapted


# ---------------------------------------------------------------------------
# T7: verify() graceful fail
# ---------------------------------------------------------------------------


def test_hermes_verify_fails_gracefully_on_missing_pem(
    caas, tmp_path: Path
) -> None:
    """If GITHUB_APP_PRIVATE_KEY_PATH points at a nonexistent file, verify()
    returns (False, message) instead of raising."""
    env_text = (
        "GITHUB_APP_ID=1\n"
        "GITHUB_APP_PRIVATE_KEY_PATH=/nonexistent/file.pem\n"
        "GITHUB_APP_INSTALLATION_ID=99\n"
    )
    ok, msg = caas._hermes_verify(env_text, "testagent", tmp_path)
    assert ok is False
    # Either "PEM not found" (if the file doesn't exist) or "PyJWT" (if
    # the import is broken) is an acceptable graceful failure
    assert "PEM not found" in msg or "PyJWT" in msg, f"unexpected msg: {msg!r}"


def test_hermes_verify_fails_gracefully_on_missing_keys(
    caas, tmp_path: Path
) -> None:
    """If the env text is missing GITHUB_APP_ID, verify() returns (False, msg)."""
    env_text = "GITHUB_APP_PRIVATE_KEY_PATH=/tmp/whatever\n"
    ok, msg = caas._hermes_verify(env_text, "testagent", tmp_path)
    assert ok is False
    assert "missing" in msg.lower()


# ---------------------------------------------------------------------------
# Adapter registration
# ---------------------------------------------------------------------------


def test_hermes_consumer_is_registered(caas) -> None:
    """CONSUMERS registry has the hermes adapter with the expected hooks."""
    assert "hermes" in caas.CONSUMERS
    adapter = caas.CONSUMERS["hermes"]
    assert adapter.name == "hermes"
    assert adapter.adapt is not None
    assert adapter.post_install is not None
    assert adapter.verify is not None


def test_adapt_handles_no_pem_gracefully(caas) -> None:
    """adapt() on env text without a PEM is a no-op (idempotent, no error)."""
    env_text = "GITHUB_APP_ID=1\nGITHUB_APP_SLUG=test\n"
    result = caas._hermes_adapt(env_text, "testagent", Path("/tmp"))
    assert result == env_text
