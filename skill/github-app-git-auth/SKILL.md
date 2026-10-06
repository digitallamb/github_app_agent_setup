---
name: github-app-git-auth
description: Authenticate git pull and push operations as a specific agent's GitHub App. Use when an agent needs to interact with a GitHub repo via git, when configuring per-agent identity on a new host, or when wiring a cron job to push to GitHub. Works alongside kennyg/gh-app-create.
license: MIT
compatibility: Python 3.10+, PyJWT
metadata:
  hermes:
    tags: [github, git, authentication, github-app, per-agent-identity]
    related_skills: []
---

# GitHub App Git Authentication

Authenticate a Hermes agent's git operations as that agent's dedicated GitHub App. Companion to the `kennyg/gh-app-create` workflow: this skill handles the **consumption** half — once a tool like `create_agent_apps.py` has created the App and populated the agent's `.env`, this skill lets the agent actually use it for `git pull` / `git push` with per-agent attribution in the commit log.

## When to Use

- You're configuring a fresh Hermes host and need an agent to push/pull to a GitHub repo.
- You want per-agent identity in commits (`csenteninels-savant[bot]`), not a single shared machine user.
- You have the App already created and installed on the target repo, and the three contract keys are in the agent's `.env`.
- You're wiring a cron job to push to GitHub and want clean per-agent attribution.

**Don't use for:** GitLab repos (use a deploy token or SSH key). PAT-only setups where `git push` "just works" with no script (use `git config credential.helper store` with a PAT, no helper needed). Long-lived secrets on disk — this skill deliberately does not produce them.

## How It Works

The 5-step chain:

1. Agent's `.env` carries three keys: `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH` (path to a `0600` PEM on disk), `GITHUB_APP_INSTALLATION_ID`.
2. The helper script reads those keys, loads the PEM, mints a 10-minute JWT signed with the App's private key.
3. The helper POSTs the JWT to `https://api.github.com/app/installations/{id}/access_tokens` and receives a 1-hour installation token (`ghs_...`).
4. The helper writes a one-shot `askpass` shell script (mode `0700`) that returns the installation token to git on demand.
5. The agent runs `git -c credential.helper=!<askpass> <git-cmd>`. Git calls the askpass, authenticates, completes the operation. The token never touches a long-lived file or shell history.

**Result:** Every push shows up on github.com as `<app-slug>[bot] <app-id>+<app-slug>[bot]@users.noreply.github.com>`. Per-agent identity, per-agent scopes, per-agent rotation. No shared secrets on disk.

## Prerequisites

- A GitHub App with `contents:write` (or `contents:read` for read-only agents) — create with `create_agent_apps.py` (this repo) or `gh app-create` directly
- The App installed on the target GitHub repo
- The **installation ID** (a number; visible in the install URL after `installations/`)
- `PyJWT` installed: `pip install PyJWT`
- The agent's `~/.hermes/profiles/<agent>/.env` writable, mode `0600`
- The PEM file at the path the `.env` references, mode `0600`

## Quick Reference

```bash
# Install the helper into an agent's runtime
cp skill/github-app-git-auth/scripts/github_auth.py \
   ~/.hermes/profiles/<agent>/scripts/

# Verify the auth chain end-to-end (do this before any git push)
python3 ~/.hermes/profiles/<agent>/scripts/github_auth.py --verify <agent>

# Run a git command authenticated as the agent
python3 ~/.hermes/profiles/<agent>/scripts/github_auth.py <agent> push github main

# Add a remote to the local repo
cd /path/to/repo
git remote add github https://github.com/<org>/<repo>.git
```

**From Python:**

```python
from github_auth import git_as_agent, verify_github_app

# Verify
ok, msg = verify_github_app("savant")
assert ok, msg

# Push
result = git_as_agent("savant", "push", "github", "main")
if result.returncode != 0:
    raise RuntimeError(f"push failed: {result.stderr}")
```

## Procedure

### Step 1: Verify the agent's `.env` has the three contract keys

**Done when:** the file contains all three keys below and `chmod 600` confirms.

```bash
GITHUB_APP_ID=<numeric>
GITHUB_APP_PRIVATE_KEY_PATH=/absolute/path/to/app.pem    # mode 0600
GITHUB_APP_INSTALLATION_ID=<numeric>
```

The PEM must be the same key GitHub has on file for the App. If you generated the App via `create_agent_apps.py` (this repo), the tool can extract the inline PEM to a `0600` file via `--consumers hermes` and will set the path correctly.

### Step 2: Install the helper script

**Done when:** the script exists at `~/.hermes/profiles/<agent>/scripts/github_auth.py` and the import succeeds.

```bash
# From the cloned repo
cp skill/github-app-git-auth/scripts/github_auth.py \
   ~/.hermes/profiles/<agent>/scripts/

# Or, if you're using create_agent_apps.py to do the full flow:
python3 create_agent_apps.py --org <org> --agent <agent> --consumers hermes --install
# The --install flag copies the helper into the agent's scripts/ dir.
```

### Step 3: Verify the auth chain before any git operation

**Done when:** the verify step returns `OK:` and prints the App slug, install ID, and a non-zero repo count.

```bash
python3 ~/.hermes/profiles/<agent>/scripts/github_auth.py --verify <agent>
# Expected output:
# OK: App slug=csenteninels-savant, install 168023791 active, 1 repos visible
```

This is the same chain we proved end-to-end against live GitHub: GET `/app` returns the App identity, POST `/app/installations/{id}/access_tokens` mints an installation token, GET `/installation/repositories` confirms the App can see the target repo. If any step returns non-200, the verify fails with a specific message — do not proceed to git operations until it passes.

### Step 4: Add a remote to the local repo

**Done when:** `git remote -v` shows a `github` remote alongside any existing `origin`.

```bash
cd /path/to/repo
git remote add github https://github.com/<org>/<repo>.git
git remote -v
```

For dual-write scenarios (parallel push to GitLab + GitHub during a migration), keep `origin` pointing at the legacy remote and use `github` for the new one.

### Step 5: Call `git_as_agent()` from the agent's pushing code

**Done when:** a real `git push` shows up on github.com with the agent's `[bot]` identity as the author.

```python
from github_auth import git_as_agent

# Push
result = git_as_agent("savant", "push", "github", "main")
if result.returncode != 0:
    raise RuntimeError(f"push failed: {result.stderr}")

# Pull (read-only agent)
result = git_as_agent("audit", "pull", "--rebase", "origin", "main")
```

Or from the shell:

```bash
python3 ~/.hermes/profiles/<agent>/scripts/github_auth.py savant push github main
```

**Verify on github.com:** the commit's author should be `<app-slug>[bot] <app-id>+<app-slug>[bot]@users.noreply.github.com>`.

## Pitfalls

- **PEM not at the path the .env says it is.** The script reads the path from the env var; if the PEM was moved or the var is stale, `verify_github_app()` will say "PEM not found at..." with the exact path. Move the file or update the var.
- **PEM mode is wrong.** GitHub doesn't care, but `verify_github_app()` checks for `0o600` and fails with a specific message. `chmod 600 <path>` to fix.
- **App installed on the wrong org/repo.** `verify_github_app()` returns the repo count — if it's `0`, the install is on a different org than you expected. Re-check the install URL.
- **Token mint works but the install doesn't see the target repo.** The install is per-`(App, account)`; the token gives you access to every repo in the install, not a specific one. If a repo isn't showing, it's not in the install's repository selection.
- **Long-running sessions caching tokens.** Don't. The 200ms re-mint cost is fine for any cron-style push. Caching opens a stale-token window.
- **Two agents pushing at the same time.** Safe — they have different App identities, different tokens, no contention.
- **The askpass script lingers in /tmp.** The script does `os.unlink` in the `finally` block. If the git process is killed (`-9`), the file may persist until `/tmp` cleanup. Acceptable risk; the token is already 1-hour-TTL.
- **Vault dual-push race.** If you push to `origin` (GitLab) and `github` back-to-back and another agent pushes to GitHub between them, the GitHub push will fast-forward cleanly. If they push to GitHub first, the second push will fail with `non-fast-forward` — handle that in the calling code (fetch + rebase + retry, or surface as an error to a human).

## Verification Checklist

After installing on a new host:

- [ ] The three contract keys are in `~/.hermes/profiles/<agent>/.env` (mode 0600)
- [ ] The PEM file at `GITHUB_APP_PRIVATE_KEY_PATH` exists and is mode 0600
- [ ] `python3 github_auth.py --verify <agent>` returns `OK: App slug=..., install N active, M repos visible`
- [ ] `git -C <repo> remote -v` shows a `github` remote pointing at the target GitHub repo
- [ ] `python3 github_auth.py <agent> push github <branch>` returns `returncode=0`
- [ ] On github.com, the new commit's author is `<app-slug>[bot] <app-id>+<app-slug>[bot]@users.noreply.github.com>`
- [ ] No long-lived token is anywhere on disk (`find / -name "*.token" -o -name "ghs_*" 2>/dev/null` should be empty)

## Files in This Skill

- `SKILL.md` — this file
- `scripts/github_auth.py` — the helper (standalone, copy to agent's scripts/)
- `references/contract.md` — the three-key env contract, with rationale for each key
- `references/decision-tree.md` — when to use this skill vs. a PAT vs. an SSH key

## Related

- `create_agent_apps.py` (this repo) — generates Apps and populates `.env`. The `--consumers hermes` flag uses this skill's contract; the new `--install` flag drops the helper into the agent's runtime.
- `references/contract.md` — the precise env contract this skill expects.
- `references/decision-tree.md` — App + helper vs. PAT vs. SSH key trade-offs.
