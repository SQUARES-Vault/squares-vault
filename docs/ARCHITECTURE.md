# Architecture

The authoritative architecture document is the **Architecture Blueprint** located at the root of the SQUARES Vault workspace:

[`SQUARES_Vault_Architecture_Blueprint.md`](../../SQUARES_Vault_Architecture_Blueprint.md)

This file is the primary reference for all decisions.  Sections marked **LOCKED** are non-negotiable.  Sections marked **DEFAULT** are configurable starting values.

## Directory guide

| Path | Purpose |
|---|---|
| `src/squares_vault/config.py` | Typed settings loader |
| `src/squares_vault/logging_setup.py` | Structured logging + scrubbing |
| `src/squares_vault/db/` | SQLite connection, migrations, repositories, models |
| `src/squares_vault/security/` | Envelope encryption, PIN, rate limits, masking |
| `src/squares_vault/bot/` | Telegram router, middlewares, handlers |
| `src/squares_vault/flows/` | Conversation flows (onboarding, add, find, …) |
| `src/squares_vault/drive/` | Google Drive OAuth + client |
| `src/squares_vault/ai/` | AI extraction pipeline |
| `src/squares_vault/reminders/` | Reminder scheduler and sender |
| `src/squares_vault/search/` | FTS search and ranking |
| `src/squares_vault/admin/` | Admin Telegram commands |
| `src/squares_vault/ops/` | Backup, restore, health |
| `src/squares_vault/i18n/` | User-facing strings (en.json) |
| `deploy/` | systemd units, install and rollback scripts |
| `docs/` | RUNBOOK, ASSUMPTIONS, privacy policy draft |
| `tests/` | Unit, integration, golden tests |
