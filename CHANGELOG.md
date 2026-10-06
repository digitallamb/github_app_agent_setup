# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-04

### Added

- **`skill/github-app-git-auth/` directory** containing the consumption
  half of the per-agent GitHub App workflow: a `SKILL.md` with the
  full per-host recipe, a `scripts/github_auth.py` helper that
  mints short-lived installation tokens and hands them to git via a
  one-shot askpass script, and two `references/` docs (the env
  contract, and a decision tree for App vs. PAT vs. SSH key auth).
- **`--install` flag** on `create_agent_apps.py` that copies the
  helper script into the agent's runtime at
  `~/.hermes/profiles/<agent>/scripts/github_auth.py`. Implied
  when `--consumers hermes --verify` are both set.
- **`install` field in the `--json` output** (boolean: was install
  requested) and per-agent `install` field (string: install path or
  warning message).
- **9 new pytest tests** in `tests/test_install.py` covering: the
  install function's happy path, mkdir-if-missing behavior,
  graceful failure when the skill folder is missing, the
  `--install` flag's CLI surface, the implicit-install logic
  (consumers+verify implies install), dry-run doesn't write, the
  helper's import-cleanliness, and the helper's CLI entry point.

### Changed

- **README** now leads with a "Two Halves: Creation + Consumption"
  section that frames the tool + skill as one workflow, and adds
  a "For New Host Operators" section with the standalone install
  command for users who already have Apps but need to wire up a
  fresh host.
- **JSON output example** in README updated to include the new
  `install` field.

## [0.2.0] - 2026-10-04

### Added

- **`--consumers <name>` flag** (repeatable) for adapting the raw
  `gh-app-create` output for a specific agent harness. Currently
  supported: `hermes`. The hermes adapter extracts the inline PEM to
  a `0600` file, prompts the user to install the App and paste the
  resulting install URL, and (with `--verify`) confirms the App is
  usable end-to-end.
- **`--verify` flag** mints a JWT, calls `GET /app` to confirm App
  identity, then calls `POST /app/installations/{id}/access_tokens`
  to confirm the install is actually live, then `GET /installation/repositories`
  to prove the install token can see real repos. Implied by
  `--consumers hermes`.
- **`--resume` flag** skips agents that have already been created
  successfully in a previous run. State is persisted in
  `<output-dir>/.state.json` (mode `0600` — contains credentials).
  Failed agents are retried; successful ones are not re-created.
- **`--json` flag** emits a single JSON object on stdout summarising
  the run (`succeeded`, `failed`, `consumers`, `verify`, `resume`),
  suitable for CI consumption. Human-readable progress output goes
  to stderr.
- **`--no-input` flag** skips interactive prompts (e.g. the
  install-URL collection step in `--consumers hermes`). Useful for CI.
- **`ConsumerAdapter` dataclass** for writing new harness adapters.
  Each adapter has four optional hooks: `adapt`, `on_disk`,
  `post_install`, `verify`. New harnesses are 30-50 lines each.
- **29 pytest tests** under `tests/` covering CLI surface, hermes
  adapter (including a realistic-env round-trip against a real
  `gh-app-create` output shape), and resume state persistence.
  Run with `python3 -m pytest tests/`.

### Fixed

- **`--dry-run` no longer requires `gh` to be installed.** Previously
  the `which("gh")` check fired before the dry-run guard, so
  `create_agent_apps.py --dry-run --agent foo` failed on machines
  without gh. Moved the check behind `not args.dry_run`.
- **State file uses mode `0600`.** Contains full credentials and must
  not be world-readable.
- **State file refuses to silently misinterpret a future
  `STATE_VERSION`.** Raises `SystemExit` with a clear message
  instead of loading data with an unknown shape.
- **`--json` output is now emitted even when `--resume` filters out
  all agents.** Previously an early-return skipped the summary.
- **The `[resume]` log line is on stderr in `--json` mode.** Moved
  the stdout-swap before `_filter_pending()` so the swap covers
  all human chatter. stdout is now a clean JSON object.

### Security

- The `.state.json` file is created with mode `0600` because it
  contains the same credentials as the per-agent `.env` files. Do
  not commit or share it. The `.gitignore` should be updated to
  cover it; this release adds the entry.

## [0.1.0] - 2025-XX-XX

Initial release. Single-file Python script that creates one GitHub App
per agent using the `kennyg/gh-app-create` `gh` CLI extension. Supports
batch from a file or stdin, single-agent mode via `--agent`, output
formats (`yaml`/`json`/`env`/`github-actions`), preset selection, and
`--dry-run` for previews.

[Unreleased]: https://github.com/digitallamb/github_app_agent_setup/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/digitallamb/github_app_agent_setup/compare/167f4f3...v0.2.0
[0.1.0]: https://github.com/digitallamb/github_app_agent_setup/releases/tag/167f4f3
