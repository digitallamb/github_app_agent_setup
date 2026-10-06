"""Shared pytest fixtures."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import the script as a module
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "create_agent_apps.py"


@pytest.fixture(scope="session")
def caas():
    """Import create_agent_apps.py as a Python module.

    Registers it in sys.modules so @dataclass introspects correctly.
    """
    # Make sure the repo root is on sys.path so future imports work
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    spec = importlib.util.spec_from_file_location("caas", str(SCRIPT_PATH))
    assert spec is not None, f"could not load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["caas"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# Fake env files that look like real gh-app-create output
# ---------------------------------------------------------------------------

FAKE_GH_APP_CREATE_ENV = """\
GITHUB_APP_ID=12345
GITHUB_APP_SLUG=test-agent
GITHUB_APP_CLIENT_ID=Iv23abc
GITHUB_APP_CLIENT_SECRET=secret123
GITHUB_APP_WEBHOOK_SECRET=hooksecret456
GITHUB_APP_PRIVATE_KEY='-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAtest
-----END RSA PRIVATE KEY-----'
"""


REALISTIC_GH_APP_CREATE_ENV = """\
GITHUB_APP_ID=5183131
GITHUB_APP_SLUG=csenteninels-savant
GITHUB_APP_CLIENT_ID=Iv23liBfkFDRQ3gbhKZ0
GITHUB_APP_CLIENT_SECRET=9b181eb83cfae29b4cc758ff7f768abf31f910db
GITHUB_APP_WEBHOOK_SECRET=39724e5c10c89cc13c41282f1ea6704397386649
GITHUB_APP_PRIVATE_KEY='-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAtest-realistic-key-content
-----END RSA PRIVATE KEY-----'
"""


@pytest.fixture
def fake_env_file(tmp_path: Path) -> Path:
    """A fake .creds/<name>.env shaped like real gh-app-create output."""
    env_file = tmp_path / "testagent.env"
    env_file.write_text(FAKE_GH_APP_CREATE_ENV, encoding="utf-8")
    os.chmod(env_file, 0o600)
    return env_file


@pytest.fixture
def realistic_env_file(tmp_path: Path) -> Path:
    """The exact env format the user's real gh-app-create run produces."""
    env_file = tmp_path / "csenteninels-savant.env"
    env_file.write_text(REALISTIC_GH_APP_CREATE_ENV, encoding="utf-8")
    os.chmod(env_file, 0o600)
    return env_file


# ---------------------------------------------------------------------------
# Pre-populated state files for --resume tests
# ---------------------------------------------------------------------------


@pytest.fixture
def pre_completed_state(tmp_path: Path) -> Path:
    """A .creds/ tree where 'alpha' has already been created successfully.

    Simulates a prior --resume-able run: state.json lists alpha in `created`,
    and the .env file exists so the resume filter has something to skip.
    """
    state = {
        "version": 1,
        "created": {"alpha": {"full_name": "alpha", "ts": 1700000000, "output_file": str(tmp_path / "alpha.env")}},
        "failed": {},
    }
    (tmp_path / ".state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "alpha.env").write_text("GITHUB_APP_ID=1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def pre_failed_state(tmp_path: Path) -> Path:
    """A .creds/ tree where 'alpha' previously failed and 'beta' succeeded."""
    state = {
        "version": 1,
        "created": {"beta": {"full_name": "beta", "ts": 1700000000, "output_file": str(tmp_path / "beta.env")}},
        "failed": {"alpha": {"stage": "create", "ts": 1700000000, "message": "browser closed"}},
    }
    (tmp_path / ".state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "beta.env").write_text("GITHUB_APP_ID=2\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# CLI runner that always strips gh from PATH so --dry-run tests are honest
# ---------------------------------------------------------------------------


@pytest.fixture
def no_gh_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run subprocesses with PATH that doesn't include the gh binary.

    Proves --dry-run doesn't depend on gh being installed.
    """
    paths = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    cleaned = [p for p in paths if "gh" not in os.path.basename(p) if p]
    monkeypatch.setenv("PATH", os.pathsep.join(cleaned) or "/usr/bin:/bin")
