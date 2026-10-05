#!/usr/bin/env python3
"""Create one GitHub App per agent in your fleet using the kennyg/gh-app-create
extension.

Agent names can be supplied either as a file (one per line; lines starting
with '#' and blank lines are ignored) or as a single name via ``--agent``.
Both modes accept ``-`` to read from stdin. For each agent, the script
invokes ``gh app-create create`` which opens a browser tab to confirm
creation on GitHub, then writes the resulting credentials (app id, private
key, client id, client secret, webhook secret) to a per-agent file.

Optionally adapts the resulting credentials for a specific agent harness
(``--consumers hermes``) and verifies the App is usable end-to-end
(``--verify``). Run state is persisted in ``.creds/.state.json`` so an
interrupted batch can be resumed with ``--resume`` instead of re-creating
already-successful Apps.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# State persistence (--resume)
# ---------------------------------------------------------------------------

STATE_FILENAME = ".state.json"
STATE_VERSION = 1


def _state_path(output_dir: Path) -> Path:
    return output_dir / STATE_FILENAME


def _load_state(output_dir: Path) -> dict[str, Any]:
    """Load the per-output-dir state file. Returns empty state if missing/corrupt."""
    path = _state_path(output_dir)
    if not path.is_file():
        return {"version": STATE_VERSION, "created": {}, "failed": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt state should never block a new run; the user can rm the
        # file if they want a true clean slate.
        return {"version": STATE_VERSION, "created": {}, "failed": {}}
    if data.get("version") != STATE_VERSION:
        # Future-proofing: refuse to silently misinterpret a newer format.
        raise SystemExit(
            f"State file {path} has version {data.get('version')!r} but this "
            f"script expects {STATE_VERSION}. Bump STATE_VERSION or delete "
            f"the state file to start fresh."
        )
    return data


def _save_state(output_dir: Path, state: dict[str, Any]) -> None:
    path = _state_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # mode 0600: state contains full app credentials in "created" entries
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
    except Exception:
        # Don't leak the partial write
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def _filter_pending(names: list[str], state: dict[str, Any], *, resume: bool) -> list[str]:
    """Drop names that already succeeded when --resume is set; else include all."""
    if not resume:
        return names
    already = set(state.get("created", {}).keys())
    pending = [n for n in names if n not in already]
    skipped = [n for n in names if n in already]
    if skipped:
        print(f"[resume] {len(skipped)} agent(s) already created, skipping: {skipped}")
    return pending


# ---------------------------------------------------------------------------
# Consumer adapters
# ---------------------------------------------------------------------------


@dataclass
class ConsumerAdapter:
    """How to adapt the raw gh-app-create output for a specific agent harness.

    Each adapter has four hooks, all optional. Run order is:
        1. adapt()       — modify the env text in-place (rename keys, etc.)
        2. on_disk()     — write auxiliary files (e.g. PEM to mode-0600 file)
        3. post_install() — pause for the user to install the App, then read install ID
        4. verify()       — mint JWT, call /app and /app/installations, return ok
    """

    name: str
    adapt: Any = None
    on_disk: Any = None
    post_install: Any = None
    verify: Any = None


def _hermes_adapt(env_text: str, full_name: str, output_dir: Path) -> str:
    """Adapt gh-app-create env output to Hermes's _try_github_app() contract.

    The consumer (tools/skills_hub.py:_try_github_app in hermes-agent) reads:

        GITHUB_APP_ID                  (literal value, OK as-is)
        GITHUB_APP_PRIVATE_KEY_PATH    (a path to a PEM on disk, NOT the inline PEM)
        GITHUB_APP_INSTALLATION_ID     (a numeric ID, only known after install)

    gh-app-create produces an inline ``GITHUB_APP_PRIVATE_KEY='...'`` value,
    which is correct for some consumers (GitHub Actions, OAuth flows) but
    wrong for Hermes. This function extracts the inline PEM, writes it to
    ``<output_dir>/<full_name>.pem`` with mode 0600, and replaces the inline
    block with a ``GITHUB_APP_PRIVATE_KEY_PATH=...`` line.
    """
    # Match the multi-line single-quoted PEM block. python-dotenv-style
    # quoting: '...' can contain newlines literally.
    pattern = re.compile(
        r"^GITHUB_APP_PRIVATE_KEY='(?P<pem>-----BEGIN[^-]+-----.*?-----END[^-]+-----)'"
        r"\s*$",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(env_text)
    if not match:
        # Already adapted, or no PEM present. Idempotent: no-op.
        return env_text

    pem_text = match.group("pem")
    pem_path = output_dir / f"{full_name}.pem"
    pem_path.write_text(pem_text, encoding="utf-8")
    os.chmod(pem_path, 0o600)

    return pattern.sub(
        f"GITHUB_APP_PRIVATE_KEY_PATH={pem_path}",
        env_text,
    )


def _hermes_post_install(env_text: str, full_name: str, output_dir: Path) -> str:
    """Prompt the user to install the App, then read the install ID.

    The App is created on GitHub but not installed on any org/repo until
    the user visits https://github.com/apps/<slug> and clicks "Install".
    The install ID appears in the resulting URL.

    Two collection modes:
        - interactive: prompt the user to paste the install URL or just the ID
        - non-interactive (--no-input): bail with a clear next-step message
    """
    # Extract the slug from the env text we just wrote.
    slug_match = re.search(r"^GITHUB_APP_SLUG=(\S+)\s*$", env_text, re.MULTILINE)
    slug = slug_match.group(1) if slug_match else full_name

    print(f"\n>>> {full_name}: App created but NOT YET installed.")
    print(f"    Visit https://github.com/apps/{slug} and click 'Install'.")
    print(f"    After installing, paste the full URL or just the trailing ID.")

    try:
        user_input = input(f"    install URL or ID (or press Enter to skip): ").strip()
    except EOFError:
        user_input = ""

    if not user_input:
        print(f"    [skipped] You can add it later by appending to {output_dir / (full_name + '.env')}:")
        print(f"        GITHUB_APP_INSTALLATION_ID=<the ID>")
        return env_text

    # Accept either a full URL or a bare number
    id_match = re.search(r"(\d{6,})", user_input)
    if not id_match:
        print(f"    [warn] Could not parse an install ID from {user_input!r}; skipping")
        return env_text

    install_id = id_match.group(1)

    # Append (or replace) the INSTALLATION_ID line
    if re.search(r"^GITHUB_APP_INSTALLATION_ID=", env_text, re.MULTILINE):
        env_text = re.sub(
            r"^GITHUB_APP_INSTALLATION_ID=.*$",
            f"GITHUB_APP_INSTALLATION_ID={install_id}",
            env_text,
            flags=re.MULTILINE,
        )
    else:
        env_text = env_text.rstrip() + f"\nGITHUB_APP_INSTALLATION_ID={install_id}\n"
    return env_text


def _hermes_verify(env_text: str, full_name: str, output_dir: Path) -> tuple[bool, str]:
    """Verify the App is usable end-to-end.

    Reads the (already-adapted) env file, mints a JWT, calls GET /app to
    confirm App identity, and if an INSTALLATION_ID is present, calls
    POST /app/installations/{id}/access_tokens to confirm the install is
    actually live.

    Returns (ok, message). On failure, message is human-actionable.
    """
    try:
        import jwt as pyjwt  # type: ignore
    except ImportError:
        return False, "PyJWT not installed (pip install PyJWT)"

    values: dict[str, str] = {}
    for line in env_text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")

    app_id = values.get("GITHUB_APP_ID")
    pem_path = values.get("GITHUB_APP_PRIVATE_KEY_PATH")
    install_id = values.get("GITHUB_APP_INSTALLATION_ID")

    if not (app_id and pem_path):
        return False, f"missing GITHUB_APP_ID or GITHUB_APP_PRIVATE_KEY_PATH in env"

    pem_file = Path(pem_path)
    if not pem_file.is_file():
        return False, f"PEM not found at {pem_file}"

    try:
        private_key = pem_file.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"can't read {pem_file}: {e}"

    now = int(time.time())
    try:
        encoded = pyjwt.encode(
            {"iat": now - 30, "exp": now + 600, "iss": app_id},
            private_key, algorithm="RS256",
        )
    except Exception as e:
        return False, f"JWT mint failed: {e}"

    headers = {
        "Authorization": f"Bearer {encoded}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "github_app_agent_setup-verify",
    }

    # Step 1: GET /app — proves the JWT is valid and the App exists
    try:
        req = urllib.request.Request("https://api.github.com/app", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            app = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        return False, f"GET /app failed: HTTP {e.code} {body}"
    except Exception as e:
        return False, f"GET /app failed: {e}"

    actual_slug = app.get("slug", "?")
    if actual_slug != values.get("GITHUB_APP_SLUG"):
        # Not fatal — slug comes from create-time metadata, this is just a
        # hint that something might be off.
        print(f"    [note] /app returned slug={actual_slug!r} (env says {values.get('GITHUB_APP_SLUG')!r})")

    # Step 2: if we have an install ID, try to mint an installation token
    if install_id:
        try:
            req = urllib.request.Request(
                f"https://api.github.com/app/installations/{install_id}/access_tokens",
                data=b"",
                headers={**headers, "Content-Length": "0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                tok = json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            if e.code in (404, 410):
                return False, (
                    f"App OK (slug={actual_slug}) but install {install_id} not found. "
                    f"Did you complete the install at https://github.com/apps/{actual_slug}?"
                )
            if e.code in (401, 403):
                return False, f"App OK but install access denied (HTTP {e.code}): {body}"
            return False, f"install token mint failed: HTTP {e.code} {body}"
        except Exception as e:
            return False, f"install token mint failed: {e}"

        install_token = tok.get("token", "")
        if not install_token.startswith("ghs_"):
            return False, f"install token has unexpected format: {install_token[:8]}..."

        # Step 3: use the install token to prove we can see the world
        try:
            req = urllib.request.Request(
                "https://api.github.com/installation/repositories",
                headers={
                    **headers,
                    "Authorization": f"Bearer {install_token}",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                repos = json.loads(r.read())
        except Exception as e:
            return False, f"install token works but /installation/repositories failed: {e}"

        return True, (
            f"App slug={actual_slug}, install {install_id} active, "
            f"{repos.get('total_count', 0)} repos visible"
        )

    # No install ID: only the App identity was confirmed
    return True, f"App slug={actual_slug} (no install ID provided; install step not verified)"


CONSUMERS: dict[str, ConsumerAdapter] = {
    "hermes": ConsumerAdapter(
        name="hermes",
        adapt=_hermes_adapt,
        on_disk=None,
        post_install=_hermes_post_install,
        verify=_hermes_verify,
    ),
}


# ---------------------------------------------------------------------------
# Existing helpers (unchanged in spirit; lifted to module scope for testability)
# ---------------------------------------------------------------------------


def _resolve_names(value: str) -> list[str]:
    """Resolve a name source into a list of names.

    If ``value`` is '-', read names from stdin (one per line; '#' comments
    and blanks are skipped). Otherwise return ``[value]`` as a single
    literal name.
    """
    if value == "-":
        names: list[str] = []
        for raw in sys.stdin.read().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            names.append(line)
        return names
    return [value]


def parse_agent_names(path: Path) -> list[str]:
    """Read agent names from ``path``; skip blanks and ``#`` comments.

    If ``path`` is '-', read from stdin instead.
    """
    if str(path) == "-":
        return _resolve_names("-")

    if not path.is_file():
        raise SystemExit(f"Agents file not found: {path}")

    names: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line)

    if not names:
        raise SystemExit(f"No agent names found in {path}")

    # Detect duplicates early so the user gets a clear error before
    # opening 8 browser tabs and creating 7 redundant apps.
    seen: set[str] = set()
    dupes = {n for n in names if n in seen or seen.add(n)}  # type: ignore[func-returns-value]
    if dupes:
        raise SystemExit(f"Duplicate agent names in {path}: {sorted(dupes)}")

    return names


def ensure_extension(repo: str) -> None:
    """Install the gh extension if it isn't already present."""
    result = subprocess.run(
        ["gh", "extension", "list"],
        capture_output=True, text=True, check=True,
    )
    if repo in result.stdout:
        return
    print(f"Installing gh extension {repo}...")
    subprocess.run(["gh", "extension", "install", repo], check=True)


def create_app(
    name: str,
    *,
    org: str,
    preset: str,
    fmt: str,
    output_dir: Path,
    prefix: str,
    dry_run: bool,
) -> bool:
    """Create a single GitHub App. Returns True on success."""
    full_name = f"{prefix}{name}"
    output_file = output_dir / f"{full_name}.env"
    cmd = [
        "gh", "app-create", "create",
        "--preset", preset,
        "--org", org,
        "--name", full_name,
        "--format", fmt,
        "--output", str(output_file),
    ]

    if dry_run:
        print(f"[dry-run] {' '.join(cmd)}")
        return True

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n>>> Creating {full_name} (confirm in your browser)...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"!!! {full_name}: gh exited with status {result.returncode}", file=sys.stderr)
        return False
    if not output_file.is_file():
        print(f"!!! {full_name}: command succeeded but {output_file} was not created", file=sys.stderr)
        return False
    print(f"--- wrote {output_file}")
    return True


# ---------------------------------------------------------------------------
# Post-creation pipeline (new in v0.2)
# ---------------------------------------------------------------------------


def _apply_consumers(
    output_file: Path,
    full_name: str,
    consumers: list[ConsumerAdapter],
    *,
    interactive: bool,
) -> tuple[bool, str]:
    """Run each consumer's adapt + post_install in sequence. Returns (ok, message)."""
    try:
        env_text = output_file.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"can't read {output_file}: {e}"

    for consumer in consumers:
        if consumer.adapt is not None:
            try:
                env_text = consumer.adapt(env_text, full_name, output_file.parent)
            except Exception as e:
                return False, f"{consumer.name}.adapt() raised: {e}"

        if consumer.post_install is not None:
            if interactive:
                try:
                    env_text = consumer.post_install(env_text, full_name, output_file.parent)
                except Exception as e:
                    return False, f"{consumer.name}.post_install() raised: {e}"
            else:
                print(f"    [skip] {consumer.name}.post_install() (use interactive shell to collect install ID)")

        try:
            output_file.write_text(env_text, encoding="utf-8")
            os.chmod(output_file, 0o600)
        except OSError as e:
            return False, f"can't write {output_file}: {e}"

    return True, "ok"


def _verify_consumers(
    output_file: Path,
    full_name: str,
    consumers: list[ConsumerAdapter],
) -> tuple[bool, str]:
    """Run each consumer's verify hook. Returns (ok, summary)."""
    messages: list[str] = []
    all_ok = True
    try:
        env_text = output_file.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"can't read {output_file}: {e}"

    for consumer in consumers:
        if consumer.verify is None:
            continue
        try:
            ok, msg = consumer.verify(env_text, full_name, output_file.parent)
        except Exception as e:
            ok, msg = False, f"{consumer.name}.verify() raised: {e}"
        all_ok = all_ok and ok
        prefix = "[ok]   " if ok else "[FAIL] "
        messages.append(f"    {prefix}{consumer.name}: {msg}")

    return all_ok, "\n".join(messages) if messages else "no consumers requested verification"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create one GitHub App per agent listed in a file, using the "
            "kennyg/gh-app-create gh extension. Each app opens a browser "
            "tab to confirm creation on GitHub."
        ),
    )
    parser.add_argument(
        "agents_file",
        type=Path,
        nargs="?",
        help="Path to a file with one agent name per line. "
             "Lines starting with '#' and blank lines are ignored. "
             "Pass '-' to read from stdin. Mutually exclusive with --agent.",
    )
    parser.add_argument(
        "--agent",
        help="Create a single app for this agent name instead of reading "
             "from a file. Pass '-' to read the name from stdin. "
             "Mutually exclusive with the positional agents_file argument.",
    )
    parser.add_argument(
        "--org", required=True,
        help="GitHub organization slug to create the apps in.",
    )
    parser.add_argument(
        "--preset", default="opencode",
        help="gh app-create preset to use (default: opencode).",
    )
    parser.add_argument(
        "--format", dest="fmt", default="env",
        choices=["yaml", "json", "env", "github-actions"],
        help="Credential output format (default: env).",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path(".creds"),
        help="Directory to write per-agent credential files into (default: .creds).",
    )
    parser.add_argument(
        "--prefix", default="",
        help="String prepended to every agent name to form the GitHub App "
             "name (e.g. 'cs-' turns 'hermes-1' into 'cs-hermes-1'). "
             "Useful for guaranteeing app names are unique across GitHub. "
             "Default: no prefix.",
    )
    parser.add_argument(
        "--extension", default="kennyg/gh-app-create",
        help="gh extension repo to ensure is installed "
             "(default: kennyg/gh-app-create).",
    )
    parser.add_argument(
        "--skip-extension-check", action="store_true",
        help="Skip checking/installing the gh extension.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the commands that would run without invoking gh.",
    )
    # ---- new in v0.2 ----
    parser.add_argument(
        "--consumers", action="append", default=[],
        choices=sorted(CONSUMERS.keys()),
        help=(
            "Adapt credentials for a specific agent harness. "
            f"Supported: {', '.join(sorted(CONSUMERS.keys()))}. "
            "Repeatable. Default: write raw gh-app-create output."
        ),
    )
    parser.add_argument(
        "--verify", action="store_true",
        help=(
            "After creation, mint a JWT and call /app (and "
            "/app/installations/{id}/access_tokens if an install ID is "
            "present) to confirm the App is usable end-to-end. Implied "
            "by --consumers if it has a verify hook."
        ),
    )
    parser.add_argument(
        "--no-input", action="store_true",
        help=(
            "Skip interactive prompts (e.g. the install-URL collection "
            "step in --consumers hermes). Useful for CI."
        ),
    )
    parser.add_argument(
        "--resume", action="store_true",
        help=(
            "Skip agents that have already been created successfully in a "
            "previous run (tracked in <output-dir>/.state.json). "
            "Failed agents are retried; successful ones are not re-created."
        ),
    )
    parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help=(
            "Emit a single JSON object to stdout at the end summarising "
            "the run, suitable for CI consumption. Suppresses the normal "
            "human-readable progress output."
        ),
    )
    return parser


def _banner(args: argparse.Namespace, pending_count: int, source_desc: str) -> None:
    """Print the run banner. Respects --json (already redirected to stderr)."""
    if args.resume:
        print(
            f"Resuming: {pending_count} pending in "
            f"org '{args.org}' (state: {_state_path(args.output_dir)})"
        )
    else:
        print(
            f"Creating {pending_count} app(s) in org '{args.org}' "
            f"using preset '{args.preset}'"
            + (f" with prefix '{args.prefix}'" if args.prefix else "")
            + f" (source: {source_desc}):"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # --dry-run shouldn't require gh to be installed: it only echoes
    # commands. The original code gated every invocation on which("gh"),
    # which made `create_agent_apps.py --dry-run --agent foo` fail on
    # machines that don't have gh yet. Move the check after we know
    # whether dry-run was requested.
    if not args.dry_run and not shutil.which("gh"):
        raise SystemExit("gh CLI not found in PATH")

    if args.agent is not None and args.agents_file is not None:
        raise SystemExit("--agent and agents_file are mutually exclusive")

    if args.agent is not None:
        names = _resolve_names(args.agent)
        source_desc = "stdin" if args.agent == "-" else "--agent"
    else:
        if args.agents_file is None:
            raise SystemExit(
                "Provide either an agents_file path or --agent NAME "
                "(use '-' in either position to read from stdin)"
            )
        names = parse_agent_names(args.agents_file)
        source_desc = "stdin" if str(args.agents_file) == "-" else str(args.agents_file)

    # Resolve consumers
    consumers = [CONSUMERS[c] for c in args.consumers]
    # --verify is implicit if any consumer has a verify hook
    wants_verify = args.verify or any(c.verify is not None for c in consumers)

    # In --json mode, all human chatter goes to stderr so stdout is a
    # single parseable JSON object. We swap sys.stdout to sys.stderr
    # for the entire run, then restore and emit JSON at the end.
    # The swap has to happen BEFORE _filter_pending() because that
    # function prints the "[resume] N agent(s) already created" line.
    _orig_stdout = sys.stdout
    if args.json_output:
        sys.stdout = sys.stderr

    try:
        # --resume state
        if args.resume:
            state = _load_state(args.output_dir)
        else:
            state = {"version": STATE_VERSION, "created": {}, "failed": {}}
        names_to_run = _filter_pending(names, state, resume=args.resume)

        _banner(args, len(names_to_run), source_desc)

        # Even when there's no work to do, we still need to emit the JSON
        # summary (in --json mode) and return the right exit code. Set
        # up the empty result set so the post-try output code has
        # something to summarise.
        if not names_to_run:
            results: dict[str, dict[str, Any]] = {}
            succeeded: list[str] = []
            failed: list[str] = []
            exit_code = 0
            output_dir = args.output_dir
            # Fall through to the output code below.
        else:
            if not args.skip_extension_check and not args.dry_run:
                ensure_extension(args.extension)

            # Run loop
            interactive = (not args.no_input) and sys.stdin.isatty() and not args.dry_run
            results = {}
            output_dir = args.output_dir

            for name in names_to_run:
                full_name = f"{args.prefix}{name}"
                output_file = output_dir / f"{full_name}.env"

                ok = create_app(
                    name,
                    org=args.org,
                    preset=args.preset,
                    fmt=args.fmt,
                    output_dir=output_dir,
                    prefix=args.prefix,
                    dry_run=args.dry_run,
                )
                if not ok:
                    results[name] = {"ok": False, "stage": "create", "message": "gh app-create failed"}
                    state.setdefault("failed", {})[name] = {
                        "stage": "create",
                        "ts": int(time.time()),
                    }
                    _save_state(output_dir, state)
                    continue

                # Read the raw env text for state persistence
                if not args.dry_run:
                    try:
                        raw_text = output_file.read_text(encoding="utf-8")
                    except OSError as e:
                        results[name] = {"ok": False, "stage": "read", "message": str(e)}
                        continue
                else:
                    raw_text = ""

                # Apply consumer adapters
                if consumers and not args.dry_run:
                    ok, msg = _apply_consumers(
                        output_file, full_name, consumers, interactive=interactive,
                    )
                    if not ok:
                        results[name] = {"ok": False, "stage": "adapt", "message": msg}
                        state.setdefault("failed", {})[name] = {
                            "stage": "adapt", "ts": int(time.time()), "message": msg,
                        }
                        _save_state(output_dir, state)
                        continue

                # Verify (if requested)
                verify_msg = ""
                if wants_verify and not args.dry_run:
                    ok, verify_msg = _verify_consumers(output_file, full_name, consumers)
                    if not ok:
                        results[name] = {"ok": False, "stage": "verify", "message": verify_msg}
                        state.setdefault("failed", {})[name] = {
                            "stage": "verify", "ts": int(time.time()), "message": verify_msg,
                        }
                        _save_state(output_dir, state)
                        continue

                # Mark this agent as created
                if not args.dry_run:
                    state.setdefault("created", {})[name] = {
                        "full_name": full_name,
                        "output_file": str(output_file),
                        "ts": int(time.time()),
                        "consumers": [c.name for c in consumers],
                    }
                    # Successful re-run after a previous failure: clear the failed entry
                    state.get("failed", {}).pop(name, None)
                    _save_state(output_dir, state)

                results[name] = {
                    "ok": True,
                    "stage": "done",
                    "full_name": full_name,
                    "output_file": str(output_file),
                    "consumers": [c.name for c in consumers],
                    "verify": verify_msg,
                }

            # ---- Final output ----
            succeeded = [n for n, r in results.items() if r.get("ok")]
            failed = [n for n, r in results.items() if not r.get("ok")]
            exit_code = 0 if not failed else 1

        if args.json_output:
            sys.stdout = _orig_stdout
            summary = {
                "succeeded": succeeded,
                "failed": [
                    {"name": n, **results[n]} for n in failed
                ],
                "consumers": [c.name for c in consumers],
                "verify": wants_verify,
                "resume": args.resume,
            }
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            if not names_to_run:
                print("Nothing to do.")
            else:
                print(f"\nDone. {len(succeeded)} succeeded, {len(failed)} failed.")
                for n in failed:
                    r = results[n]
                    print(f"  FAILED: {n} (stage: {r.get('stage')}) — {r.get('message')}")
                if args.resume:
                    print(f"Resume state saved to {_state_path(output_dir)}")

        return exit_code
    finally:
        sys.stdout = _orig_stdout


if __name__ == "__main__":
    sys.exit(main())
