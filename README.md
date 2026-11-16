# Notchboard Actions

A small watch-only Python desktop service for Gitea Actions and GitHub Actions.
It publishes dots, a running-workflows panel, event cards and search commands over
session D-Bus for Notchboard contract v1. Notchboard decides visibility; its
extension renderer is still being built. This program works independently.

GitHub is a read-only mirror of the Gitea repository; issues live on Gitea.
No server repository is created by this project.

## Run without installing

Requires Python 3, PyGObject with Gio/GLib and libsecret introspection (Fedora 43
provides these), plus `gh` authenticated for GitHub. No pip dependencies or build.

```sh
python3 actions.py --demo                 # fake cycle; no network or credentials
python3 tools/nbstate.py                  # another terminal
python3 tools/nbstate.py --watch
python3 tools/nbstate.py --activate refresh
python3 tools/nbstate.py --activate notchboard.panel true
python3 tools/nbstate.py --activate notchboard.panel false
python3 actions.py --once --demo          # JSON without D-Bus
make test
make run                                 # real service; Ctrl-C stops it
python3 actions.py --once                 # real poll, JSON, exits nonzero on errors
```

`make state` reads the live service. Do not start two copies on the same bus.
The demo shows four synthetic runs in a 32-second cycle with new run ids each
time, so every cycle produces a start, a failure and a success card (about 11
seconds apart, Notchboard's limit). `NOTCHBOARD_ACTIONS_DEMO_STEP=2` halves the
phase length; in demo mode every `Activate` is printed to stdout.
State may contain private repository names: do not share real `--once` output.
Tests use only synthetic placeholders and need neither GNOME nor credentials.

## Optional configuration

Create `~/.config/notchboard-actions/config.json` yourself (or use
`$XDG_CONFIG_HOME/notchboard-actions/config.json`):

```json
{
  "gitea": {"url": "https://gitea.example.com", "orgs": ["my-org"]},
  "github": {
    "enabled": true,
    "repos": ["owner/name"],
    "pushed_within_days": 14
  }
}
```

Every key is optional. Missing file enables GitHub when `gh` is available.
Omit `repos` to discover the 30 most recently pushed accessible repositories,
filtered to the last 14 days, refreshed every 10 minutes. Set `enabled` false to
disable GitHub. Tokens are never stored in this config. GitHub uses
`gh auth token` at startup and retries credential lookup once on HTTP 401. Missing credentials are
looked up again on later polls, at most once a minute.

For Gitea, store a token in the GNOME keyring, entering it at the secret-tool
prompt (never put a token on the command line):

```sh
secret-tool store --label='Notchboard Actions' \
  service dev.kanterlabs.NotchboardActions host gitea.example.com
```

The `host` attribute must match the configured URL's hostname. Credentials are retried automatically. No URL or no keyring token disables Gitea only. Plain HTTP is refused except for localhost (including loopback IP addresses). Reads are GET only, with 10-second HTTP timeouts; redirects
are rejected to prevent credential forwarding. Credentials and caches stay in
memory. The service never reruns, cancels or writes workflow data.

## Behavior and boundaries

Polling uses 15 seconds with active runs, 60 seconds idle and 5 seconds with the
panel open. Errors back off exponentially to 5 minutes; Refresh requests an
immediate poll. Both sources run concurrently outside the GLib thread. Backoff and next-poll times are independent per source. Each failed endpoint
retains its own snapshot and a generic panel warning; active or queued runs
are dropped after five minutes without a successful fetch.

Failed dots persist for 10 minutes or until the panel is closed after being opened; successful dots
persist 30 seconds. Success cards require a run longer than 2 minutes. Failures
have priority in the offer queue; offers are separated by at least 10 seconds.
Queued start cards are discarded if the run has finished. Progress estimates
use the newest successful duration observed in the fetched pages, capped at 95%.
No extra historical API calls are made when that duration is unknown.

Rows are capped at 10, leaving room for status and navigation within 12 items.
Untrusted strings are capped at 512 characters and rows trimmed to the 16 KiB
budget. Both source links appear when Gitea is configured; without a Gitea URL
only the GitHub link is meaningful. State/history are ephemeral across restarts.
First successful source snapshots (and each newly discovered GitHub repository)
prime event detection silently. Later snapshots detect runs completed between
polls. Completed runs may linger based on their completion timestamps.

## API shapes

GitHub uses `workflow_runs`, `status`, `conclusion`, `run_started_at`, `updated_at`,
`html_url`, `head_branch`, `name`, `workflow_id`, `repository`, and `run_attempt`:
[GitHub workflow run REST reference](https://docs.github.com/en/rest/actions/workflow-runs).
Repository run requests cache ETags and reuse cached data on 304.

Gitea uses user and configured organisation `/actions/runs?limit=20` endpoints,
`workflow_runs` containing `started_at`, `completed_at`, `status`, `conclusion`,
`repository.full_name`, `html_url`, `head_branch`, `path`, `display_title`,
`run_number`, and integer `run_attempt`:
[Gitea user workflow runs](https://docs.gitea.com/api/operations/get-user-workflow-runs/).
The synthetic fixture follows the reviewed Gitea 28.x ActionWorkflowRun shape.
Gitea does not provide `name` or `workflow_id`: these are derived from the
filename before `@` in `path` (display name omits .yml/.yaml). Epoch timestamps
are treated as unset. Gitea sends no ETag; conditional requests are optional. Older servers without these endpoints are reported unavailable.

MIT license, © KanterLabs.
