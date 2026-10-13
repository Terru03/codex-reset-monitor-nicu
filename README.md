# Codex Reset Monitor

Standalone scheduled monitor that reads Codex account rate limits through the official Codex CLI app-server and sends a Discord ping when a quota window rolls over.

The repository contains only an encrypted copy of the isolated Codex `auth.json`. The encryption key and Discord webhook remain GitHub Actions secrets.

## GitHub Actions secrets

- `AUTH_FILE_KEY`
- `DISCORD_WEBHOOK_URL`
- `DISCORD_USER_ID`

## Schedule

The workflow checks every five minutes, offset from the top of the hour. The first successful run seeds state and does not send a reset alert.

## Offline regression tests

Run from the repository root with Python 3.9 or newer:

```sh
python -B -m unittest discover -s tests -v
```

The tests use temporary state files and replace the account reader and network boundaries. They do not read authentication files, send Discord messages, or modify the checked-in monitor state. Python needs a timezone database; on Windows, install `tzdata` if `Europe/Bucharest` is unavailable.

Coverage includes initial seeding, quota/reset transitions, invalid usage values, state reloads, partial notification delivery, retry after usage resumes, status-publish failures and reset alignment. Failed notifications remain in `pendingAlerts` until delivery succeeds. A crash after Discord accepts a message but before the state write can still cause a duplicate; the webhook API and local state write are not one transaction.
