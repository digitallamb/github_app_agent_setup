#!/usr/bin/env python3
"""GitHub App authentication helper for per-agent git operations.

Companion to skill/github-app-git-auth/SKILL.md.

Mints a 1-hour installation token from the agent's .env credentials,
then hands it to git via a one-shot askpass script. Per-call token
generation; no long-lived secrets on disk.

Usage:
    from github_auth import git_as_agent, verify_github_app

    # Verify the auth chain (do this before any push):
    ok, msg = verify_github_app("savant")
    assert ok, msg

    # Run a git command authenticated as the agent:
    result = git_as_agent("savant", "push", "github", "main")
    print(result.stdout, result.stderr)

CLI form:
    python3 github_auth.py --verify <agent>
    python3 github_auth.py <agent> <git args...>

Dependencies: PyJWT (pip install PyJWT)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt


def _load_env(agent_profile: str) -> dict[str, str]:
    """Read the agent's .env file. Returns only KEY=VALUE lines, ignoring comments."""
    env_path = Path(f"/home/hermes/.hermes/profiles/{agent_profile}/.env")
    if not env_path.is_file():
        raise RuntimeError(f".env not found at {env_path}")
    cfg = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _check_config(cfg: dict[str, str]) -> None:
    """Verify the three contract keys are present and the PEM is readable."""
    for key in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY_PATH", "GITHUB_APP_INSTALLATION_ID"):
        if not cfg.get(key):
            raise RuntimeError(f"missing {key} in agent .env")
    pem = Path(cfg["GITHUB_APP_PRIVATE_KEY_PATH"])
    if not pem.is_file():
        raise RuntimeError(f"PEM not found at {pem}")
    if oct(pem.stat().st_mode & 0o777) != "0o600":
        raise RuntimeError(f"PEM at {pem} is not mode 0600 (got {oct(pem.stat().st_mode & 0o777)})")


def mint_installation_token(agent_profile: str) -> str:
    """Mint a 1-hour installation token for the named agent's GitHub App.

    Returns the token string (ghs_...). The token is short-lived; the caller
    should not cache it across long intervals. Default usage is one token
    per git invocation, which is fast enough (~200ms) to be acceptable.
    """
    cfg = _load_env(agent_profile)
    _check_config(cfg)

    private_key = Path(cfg["GITHUB_APP_PRIVATE_KEY_PATH"]).read_text(encoding="utf-8")
    jwt_token = jwt.encode(
        {
            "iat": int(time.time()) - 30,
            "exp": int(time.time()) + 600,
            "iss": cfg["GITHUB_APP_ID"],
        },
        private_key,
        algorithm="RS256",
    )
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{cfg['GITHUB_APP_INSTALLATION_ID']}/access_tokens",
        data=b"",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-app-git-auth-skill",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())["token"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        if e.code == 404:
            raise RuntimeError(
                f"install {cfg['GITHUB_APP_INSTALLATION_ID']} not found — "
                f"is the App installed on the target repo/org? ({body})"
            ) from e
        if e.code in (401, 403):
            raise RuntimeError(
                f"auth rejected by GitHub (HTTP {e.code}): {body} — "
                f"check the App ID, PEM, and install ID"
            ) from e
        raise RuntimeError(f"token mint failed: HTTP {e.code} {body}") from e


def verify_github_app(agent_profile: str) -> tuple[bool, str]:
    """Verify the agent's GitHub App auth chain end-to-end.

    Returns (ok, message). On failure, message is human-actionable.
    """
    try:
        cfg = _load_env(agent_profile)
        _check_config(cfg)
    except Exception as e:
        return False, f"config check failed: {e}"

    try:
        token = mint_installation_token(agent_profile)
    except Exception as e:
        return False, f"token mint failed: {e}"

    # Use the install token to confirm we can see the world
    req = urllib.request.Request(
        "https://api.github.com/installation/repositories",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-app-git-auth-skill",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            repos = json.loads(r.read())
        return True, (
            f"App slug={cfg.get('GITHUB_APP_SLUG', '?')}, "
            f"install {cfg['GITHUB_APP_INSTALLATION_ID']} active, "
            f"{repos.get('total_count', 0)} repos visible"
        )
    except Exception as e:
        return False, f"install token minted but /installation/repositories failed: {e}"


def git_as_agent(agent_profile: str, *git_args: str) -> subprocess.CompletedProcess:
    """Run a git command authenticated as the named agent's GitHub App.

    Per-call token mint (~200ms). The token is delivered to git via a
    one-shot askpass script (mode 0700) that is auto-deleted after the
    git process exits. No long-lived secrets on disk.
    """
    install_token = mint_installation_token(agent_profile)

    # Write a one-shot askpass that returns the token on demand
    askpass = tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        prefix=f"github-askpass-{agent_profile}-",
        suffix=".sh",
    )
    askpass.write(f"#!/bin/sh\necho '{install_token}'\n")
    askpass.close()
    os.chmod(askpass.name, 0o700)

    try:
        return subprocess.run(
            ["git", "-c", f"credential.helper=!{askpass.name}", *git_args],
            capture_output=True,
            text=True,
        )
    finally:
        try:
            os.unlink(askpass.name)
        except OSError:
            pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: github_auth.py <agent-profile> [git args...]", file=sys.stderr)
        print("       github_auth.py --verify <agent-profile>", file=sys.stderr)
        sys.exit(2)
    if sys.argv[1] == "--verify":
        if len(sys.argv) != 3:
            print("Usage: github_auth.py --verify <agent-profile>", file=sys.stderr)
            sys.exit(2)
        ok, msg = verify_github_app(sys.argv[2])
        print(f"{'OK' if ok else 'FAIL'}: {msg}")
        sys.exit(0 if ok else 1)
    profile = sys.argv[1]
    result = git_as_agent(profile, *sys.argv[2:])
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    sys.exit(result.returncode)
