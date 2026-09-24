# Codex Reset Monitor

Standalone scheduled monitor that reads Codex account rate limits through the official Codex CLI app-server and sends a Discord ping when a quota window rolls over.

The repository contains only an encrypted copy of the isolated Codex `auth.json`. The encryption key and Discord webhook remain GitHub Actions secrets.

## GitHub Actions secrets

- `AUTH_FILE_KEY`
- `DISCORD_WEBHOOK_URL`
- `DISCORD_USER_ID`

## Schedule

The workflow checks every five minutes, offset from the top of the hour. The first successful run seeds state and does not send a reset alert.
