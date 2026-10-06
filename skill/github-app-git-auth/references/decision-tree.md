# When to Use This Skill vs. a PAT vs. an SSH Key

There are three common ways to authenticate git operations against GitHub. Each has different trade-offs. This document helps you pick the right one for your situation.

## The three options

### Option A — GitHub App + this skill (recommended for fleets)

**What it is:** A dedicated GitHub App per agent, with a private key on the agent's host. The skill mints short-lived installation tokens per push.

**Pros:**
- Per-agent identity in commit log (`csenteninels-savant[bot]`)
- Per-agent scopes (write for `archive`, read-only for `audit`)
- Per-agent rotation: if one App's key leaks, rotate just that one
- No long-lived secrets on disk
- Per-agent audit trail in GitHub's UI

**Cons:**
- ~30 lines of helper code (this skill)
- Token must be minted per push (~200ms)
- App must be installed on the target repo before push works

**Right for:** Fleets of 2+ agents, per-agent audit needs, security-conscious setups, anything where per-agent attribution matters.

### Option B — Personal Access Token (PAT) in a credential helper

**What it is:** A long-lived token from `https://github.com/settings/tokens`, stored in `~/.git-credentials` or a credential helper.

**Setup:**
```bash
git config --global credential.helper store
echo "https://x-access-token:<PAT>@github.com" > ~/.git-credentials
chmod 600 ~/.git-credentials
# Then every git push just works.
```

**Pros:**
- Zero scripting after initial setup
- Fast (no per-push token mint)
- Trivial to reason about

**Cons:**
- Long-lived secret on disk (the PAT)
- All pushes share one identity (the user who created the PAT)
- Compromise = revoke + re-issue + update every consumer
- No per-agent separation (one PAT for all agents = one identity, no attribution)

**Right for:** Single-user setups, dev machines, prototyping, anything where per-agent identity is not a requirement.

### Option C — SSH key per agent

**What it is:** Generate an SSH key per agent, add the public key to a GitHub account, configure SSH to use the right key for the right remote.

**Setup (per agent):**
```bash
ssh-keygen -t ed25519 -f ~/.ssh/github-agent-<name> -C "<name>@hermes"
# Add the public key to a GitHub machine user

# ~/.ssh/config
Host github.com-<name>
    HostName github.com
    User git
    IdentityFile ~/.ssh/github-agent-<name>
    IdentitiesOnly yes

# Per repo
git remote add origin git@github.com-<name>:<org>/<repo>.git
```

**Pros:**
- No per-push scripting — `git push` works as expected
- Each agent's key can be revoked independently
- Works with any SSH-capable tool, not just Python

**Cons:**
- One machine user per agent (8 users for an 8-agent fleet)
- SSH key rotation is more involved (revoke old, distribute new)
- Keys are still long-lived secrets

**Right for:** Mixed-shell/Python environments, setups where SSH is the existing auth model, anything where the per-push cost of App auth is unacceptable.

## Comparison table

|  | App + skill (this) | PAT | SSH key per agent |
|---|---|---|---|
| `git push` "just works" after setup? | ❌ (needs the helper) | ✅ | ✅ |
| Per-agent attribution in commit log | ✅ (real `[bot]`) | ❌ (one user) | ✅ (per machine user) |
| Per-agent scopes | ✅ (real GitHub primitive) | ❌ (same token) | ✅ (per account) |
| Compromise blast radius | One App | All agents | One agent |
| Rotation friction | Low (regenerate PEM) | High (revoke + re-issue) | Medium (revoke + redistribute) |
| Long-lived secret on host | ❌ (only PEM = rotation handle) | ✅ (PAT) | ✅ (SSH key) |
| Setup work for 8 agents | 8 Apps (75 sec each via this tool) | 1 PAT + 1 account | 8 keys + 8 machine users |
| Best for | Per-agent fleet, security-first | Single user, dev setup | Mixed environments |

## When the answer is clearly one or the other

**Use the App + skill** if any of these are true:
- You have 2+ agents that need different identities in git log
- Per-agent rotation is a security requirement
- You want real GitHub `[bot]` identities in commits
- You already have `gh-app-create` or `create_agent_apps.py` in your workflow

**Use a PAT** if:
- You're a single user with one machine and no need for per-agent identity
- The overhead of an App is more than the value of per-agent attribution
- You want `git push` to "just work" with zero scripting

**Use SSH keys per agent** if:
- Your tooling isn't Python-first
- You already have an SSH-based auth model and want to stay in it
- The 200ms per-push cost of App auth is unacceptable for your workload

## The hybrid option (also valid)

You can use Apps for *write* operations (push) and PATs for *read* operations (clone, fetch) in the same fleet. The per-agent App handles the security-sensitive direction (push), and a single PAT handles the easy direction (read). This is a common pattern in CI/CD systems that need to push code as multiple identities but only need to pull from one place.

## What this skill assumes

This skill assumes you've decided on Option A (App + skill). It does not implement Option B (PAT) or Option C (SSH) — those are easier and don't need a skill. The skill exists because Option A has a non-trivial setup story (the 5-step recipe) that benefits from being captured in a reusable form.
