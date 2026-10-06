# The Three-Key Env Contract

This skill reads exactly three environment variables from the agent's `.env`. Other keys (CLIENT_ID, CLIENT_SECRET, WEBHOOK_SECRET, etc.) are inert for git operations and may be present for other consumers.

## The contract

```bash
GITHUB_APP_ID=<numeric>                                # The App's numeric ID
GITHUB_APP_PRIVATE_KEY_PATH=/absolute/path/to/app.pem # Path to the PEM, mode 0600
GITHUB_APP_INSTALLATION_ID=<numeric>                   # The install ID, post-install
```

## Why these three (and not others)

### `GITHUB_APP_ID`

The App's identity. When you call `GET /app` on the GitHub API with the App's JWT, GitHub returns the App's metadata including this ID. It is stable for the lifetime of the App.

**Where to get it:** The App's settings page (`https://github.com/settings/apps/<app-slug>`), under "About." The `gh-app-create` extension prints it on creation. `create_agent_apps.py` (this repo) with `--consumers hermes` writes it to `.env` automatically.

**Common mistake:** Using the App's *slug* (e.g. `csenteninels-savant`) instead of the numeric ID. The slug is human-readable, but the API needs the number.

### `GITHUB_APP_PRIVATE_KEY_PATH`

Path to the App's RSA private key on disk. The key never leaves your machine — you sign JWTs locally and send only the signed token to GitHub.

**Why a path, not the inline value:** The hermes consumer code (`tools/skills_hub.py:_try_github_app`) reads the file at the given path. If `gh-app-create` gave you the PEM inline in the env, you must split it out. The `--consumers hermes` flag in `create_agent_apps.py` does this automatically: it writes the PEM to `<output_dir>/<full_name>.pem` with mode `0600`.

**Why mode 0600 matters:** The key is the credential. Mode `0600` means only the file owner can read it, which matches what GitHub's auth model assumes (the App's key is the App's secret, full stop). Mode `0644` is world-readable and a security risk.

**Common mistake:** Putting the PEM inline in the `.env` and leaving the path unset. The hermes consumer won't find it.

### `GITHUB_APP_INSTALLATION_ID`

Numeric ID that identifies this specific install of the App on a specific account (user or org). Created when someone installs the App on a repo/org; visible in the install URL:

```
https://github.com/<org>/<repo>/settings/installations/168023791
                                              ^^^^^^^^^^^^
                                              this is the install ID
```

**Why it's not known at App-creation time:** The App is *created* at one moment, *installed* at a different moment (could be days, weeks, or months later, possibly by a different human). The `gh-app-create` extension creates the App but doesn't install it — installation is a separate user action in the GitHub UI. That's why the install ID is collected *after* the App exists, not as part of the creation flow.

**Common mistake:** Trying to push before the App is installed. `POST /app/installations/{id}/access_tokens` returns 404 if no install exists with that ID, or if the install exists but the requested repo isn't in its repository selection. The verify command reports the repo count — if it's `0`, the install is on the wrong account.

## What other env keys exist (and why we ignore them)

When you create an App via `gh-app-create`, the tool produces these additional keys:

| Key | What it is | Used for | Why we don't read it |
|---|---|---|---|
| `GITHUB_APP_SLUG` | Human-readable name | App identification, logs | Not a credential, just metadata |
| `GITHUB_APP_CLIENT_ID` | OAuth client ID | OAuth web flow | Not used for App auth (JWT, not OAuth) |
| `GITHUB_APP_CLIENT_SECRET` | OAuth client secret | OAuth web flow | Not used for App auth |
| `GITHUB_APP_WEBHOOK_SECRET` | HMAC secret for webhook validation | Webhook signature verification | Not used for git operations |

The skill ignores these. They are useful for OAuth-based flows (like "log in with GitHub") and webhook receivers, but for git auth (JWT-based), they're noise.

## Verifying the contract

```bash
python3 ~/.hermes/profiles/<agent>/scripts/github_auth.py --verify <agent>
# Expected: "OK: App slug=..., install N active, M repos visible"
```

If verify returns `(False, ...)`, the message names exactly which key is missing or which check failed.
