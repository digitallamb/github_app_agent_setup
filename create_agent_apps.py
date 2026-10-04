#!/usr/bin/env python3
"""Create one GitHub App per hermes agent using the kennyg/gh-app-create extension.

The list of agent names is read from a file (one per line; lines starting
with '#' and blank lines are ignored). For each agent, the script invokes
``gh app-create create`` which opens a browser tab to confirm creation on
GitHub, then writes the resulting credentials (app id, private key, client
id, client secret, webhook secret) to a per-agent file.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def parse_agent_names(path: Path) -> list[str]:
    """Read agent names from ``path``; skip blanks and ``#`` comments."""
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
        help="Path to a file with one agent name per line. "
             "Lines starting with '#' and blank lines are ignored.",
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
        help="Print the commands that would be run without invoking gh.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not shutil.which("gh"):
        raise SystemExit("gh CLI not found in PATH")

    if not args.skip_extension_check:
        ensure_extension(args.extension)

    names = parse_agent_names(args.agents_file)
    print(
        f"Creating {len(names)} app(s) in org '{args.org}' "
        f"using preset '{args.preset}'"
        + (f" with prefix '{args.prefix}'" if args.prefix else "")
        + ":"
    )
    for n in names:
        print(f"  - {args.prefix}{n}")

    results: dict[str, bool] = {}
    for name in names:
        results[name] = create_app(
            name,
            org=args.org,
            preset=args.preset,
            fmt=args.fmt,
            output_dir=args.output_dir,
            prefix=args.prefix,
            dry_run=args.dry_run,
        )

    succeeded = [n for n, ok in results.items() if ok]
    failed = [n for n, ok in results.items() if not ok]

    print(f"\nDone. {len(succeeded)} succeeded, {len(failed)} failed.")
    for n in failed:
        print(f"  FAILED: {n}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
