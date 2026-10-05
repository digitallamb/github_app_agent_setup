# github_app_agent_setup

Create one GitHub App per agent in your fleet using the
[kennyg/gh-app-create](https://github.com/kennyg/gh-app-create) `gh` CLI
extension. By default each app is created with the `opencode` preset
(issues, PRs, contents write; listens to issue comments and PR review
comments) and its credentials are written to a per-agent file. The
preset and every other parameter are configurable — see the flags table
below.

> **v0.2.0** adds consumer adapters (currently `hermes`), `--verify`,
> `--resume`, `--json`, and `--no-input` — see
> [CHANGELOG.md](CHANGELOG.md) for the full list of changes.

## Why GitHub Apps for agents

Most ad-hoc agent setups reach for personal access tokens (PATs) or a single shared bot account. Both have sharp edges at fleet scale. A dedicated GitHub App per agent is a better default:

- **Least-privilege permissions.** Each app gets exactly the scopes it
  needs (`issues:write`, `contents:write`, `pull_requests:write`, ...) and
  nothing more. No accidental repo-admin blast radius.
- **Short-lived installation tokens.** Apps mint installation tokens that
  expire (default 1 hour) instead of long-lived bearer tokens that sit in
  env files for months.
- **Per-agent identity and audit trail.** Every comment, push, or PR is
  attributed to the specific app that made it. Easy to trace misbehavior
  back to one agent and rotate just that one.
- **Independent rotation and revocation.** Compromise of one agent's
  private key doesn't cascade to the rest of the fleet — rotate one app
  without touching the others.
- **No shared secrets.** Each agent has its own private key, client
  secret, and webhook secret, so credentials aren't a coordination
  problem across the fleet.
- **Better than PATs, better than a single org bot.** PATs are tied to a
  human user; a shared bot account concentrates risk and obscures which
  agent did what.

The cost is a one-time setup per agent (what this script automates) plus
the discipline of storing the resulting credentials safely (see
[Security](#security) below).

## Compatible agent harnesses

This script handles the GitHub-side setup — the app, its permissions,
and its credentials — and optionally adapts the output for a specific
agent harness so the credentials are ready to drop into that agent's
config. Supported harnesses:

- [hermes](https://hermes-agent.nousresearch.com/) — `--consumers hermes`
  extracts the inline PEM to a `0600` file, prompts for the install
  ID, and verifies the App is usable end-to-end.
- [openclaw](https://openclaw.ai/) — base gh-app-create output works
  directly; no consumer adapter needed today.

If your harness isn't listed, it almost certainly still works as long as
you can point it at a GitHub App installation. Run without
`--consumers` to get the raw gh-app-create output and adapt by hand.

## Prerequisites

- [`gh` CLI](https://cli.github.com/) installed and authenticated
  (`gh auth status`) with permission to create apps in the target org
- Python 3.10+ (uses `dict[str, bool]` and `list[str]` annotations)
- `PyJWT` (`pip install PyJWT`) — only required if you use
  `--consumers hermes` or `--verify`

The script auto-installs the `kennyg/gh-app-create` extension on first
run. To skip this, pass `--skip-extension-check`.

## Setup

1. Edit `agents.txt` with one agent name per line. Lines starting with
   `#` and blank lines are ignored.

## Usage

For each agent, `gh` will open a browser tab asking you to confirm the
app creation on GitHub. Click through and the script will continue with
the next agent. Credentials for each app land in
`.creds/<full-name>.env`.

To preview what would run without opening any browser tabs, add
`--dry-run`.

**Batch from a file:**

```sh
python3 create_agent_apps.py --org <your-org> agents.txt
```

**Batch from stdin** (e.g. from another script or a `kubectl get` style
list of names):

```sh
printf 'forge\natlas\norion\n' | python3 create_agent_apps.py --org <your-org> -
```

**Single agent:**

```sh
python3 create_agent_apps.py --org <your-org> --agent forge
```

**Single agent from stdin** (useful in pipelines):

```sh
echo forge | python3 create_agent_apps.py --org <your-org> --agent -
```

`--agent` and the positional `agents_file` argument are mutually
exclusive. In either position, pass `-` to read from stdin.

**Adapting for a specific agent harness** (e.g. hermes):

```sh
python3 create_agent_apps.py --org <your-org> agents.txt --consumers hermes
```

This extracts the inline PEM from each `.env` to a `0600` file, prompts
you to install each App and paste the resulting install URL, and (if
PyJWT is installed) verifies the App is usable end-to-end before moving
on to the next agent.

**Resuming an interrupted batch:**

```sh
python3 create_agent_apps.py --org <your-org> agents.txt --resume
```

Successful agents are skipped; failed agents are retried. State is kept
in `.creds/.state.json`.

**CI-friendly JSON output:**

```sh
python3 create_agent_apps.py --org <your-org> --agent forge --json --dry-run
```

A single JSON object on stdout summarises the run; human chatter goes to
stderr.

## Flags

| Flag | Description |
| --- | --- |
| `agents_file` (positional, optional) | Path to a file with one agent name per line. Lines starting with `#` and blank lines are ignored. Pass `-` to read from stdin. |
| `--agent` | Create a single app for this agent name instead of reading from a file. Pass `-` to read the name from stdin. Mutually exclusive with the positional `agents_file` argument. |
| `--org` (required) | GitHub organization slug to create the apps in. |
| `--prefix` | String prepended to every agent name to form the GitHub App name. Useful for guaranteeing app names are unique across GitHub. Example: `--prefix cs-` turns `hermes-1` into `cs-hermes-1`. Default: no prefix. |
| `--preset` | `gh app-create` preset to use. Default: `opencode`. |
| `--format` | Credential output format: `yaml`, `json`, `env`, or `github-actions`. Default: `env`. |
| `--output-dir` | Directory to write per-agent credential files into. Default: `.creds`. |
| `--extension` | `gh` extension repo to ensure is installed. Default: `kennyg/gh-app-create`. |
| `--skip-extension-check` | Skip checking/installing the `gh` extension. |
| `--dry-run` | Print the commands that would run without invoking `gh`. Doesn't require `gh` to be installed. |
| `--consumers` | Adapt credentials for a specific agent harness (repeatable). Supported: `hermes`. |
| `--verify` | After creation, mint a JWT and call `/app` (and `/app/installations/{id}/access_tokens` if an install ID is present) to confirm the App is usable end-to-end. Implied by `--consumers` when the consumer has a `verify` hook. |
| `--no-input` | Skip interactive prompts (e.g. the install-URL collection step in `--consumers hermes`). Useful for CI. |
| `--resume` | Skip agents that have already been created successfully in a previous run (tracked in `<output-dir>/.state.json`). Failed agents are retried; successful ones are not re-created. |
| `--json` | Emit a single JSON object to stdout at the end summarising the run. Human chatter goes to stderr. Useful for CI. |

## Output

The script exits 0 if all apps were created successfully, 1 otherwise.
Any failures are listed in a final summary so you can re-run the script
without re-creating the ones that succeeded.

In `--json` mode, stdout is a single JSON object shaped like:

```json
{
  "consumers": ["hermes"],
  "failed": [],
  "resume": false,
  "succeeded": ["savant"],
  "verify": true
}
```

`failed` entries are objects with `name`, `ok: false`, `stage` (one of
`create`, `read`, `adapt`, `verify`), and a human-readable `message`.

## Security

The credential files contain the app's private key, client secret, and
webhook secret. Treat `.creds/` like a secrets directory:

- `.creds/` is already covered by `.gitignore` — keep it that way.
- Restrict the directory's filesystem permissions (e.g.
  `chmod 700 .creds`).
- The per-agent `.state.json` is also created with `0600` and contains
  the same credentials. Don't commit or share it.
- When loading credentials into an agent, prefer reading the env file at
  startup and letting the agent mint short-lived installation tokens
  rather than reusing the long-lived client secret.
- Rotate any agent's credentials individually if you suspect compromise
  — the per-agent isolation is the whole point.
