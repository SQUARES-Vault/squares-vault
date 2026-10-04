# Changelog

All notable changes to SQUARES Vault will be documented here (Keep a Changelog format).

---

## [Unreleased]

### M9 — Privacy and lifecycle (done)
- Full `/deletemydata` workflow executing comprehensive right-to-be-forgotten deletion across all tables.
- Implemented `/storage` and `/export` commands for data transparency.
- Secure tombstone generation for cross-device sync consistency.

### M8 — Search and retrieval (done)
- FTS5 and trigram virtual tables in SQLite for instant text search.
- Seamless synchronization with standard records via a Python wrapper.
- Natural language search interface for Telegram.

### M7 — Add/Edit flows (done)
- Interactive Draft cards for AI extracted documents with Save/Edit/Discard inline buttons.
- Fully simulated end-to-end flow testing from upload, to queue, to AI parsing, to DB saving, to reminder generation.
- 30-second undo capability after saving a draft.
- Seamless creation of schedules tied to manually reviewed drafts.

### M6 — AI extraction (done)
- Gemini REST API integration using structured JSON outputs.
- Background `ai_queue` worker loop for asynchronous document parsing.
- Global and per-user daily rate limits with next-day deferral.
- Automatic image downscaling and EXIF stripping before AI processing.
- Strict post-validation rules (Indian vehicle formats, computing warranty dates).
- Bring Your Own Key (`/byok`) flow using encrypted `user_keys`.
- Golden tests for JSON schema adherence.

### M5 — Reminders (done)
- Schedule generator with category-specific logic.
- Idempotent `claim-before-send` scheduler loop.
- Inline keyboard handlers for Snooze and Done.
- Integration test for scheduler restart and math.

### M4 — Records and manifest sync (done)
- Database repositories for CRUD operations on records and files.
- `ulid` based ID generation and base32 coded names for privacy.
- `Pillow` image processor for stripping EXIF data and resizing.
- Two-way manifest sync stub and rebuild integration test.
- Automated testing covers DB rebuilds and image processing.

### M3 — Google Drive Integration (done)
- AIOHTTP Device Auth flow implementation (`/connect`).
- AES-256-GCM envelope encryption for OAuth tokens at rest (`token_store.py`).
- Background token polling logic with timeout.
- Auto-refreshing Drive API wrapper (`api.py`).

### M2 — Onboarding (done)
- FSM for `/start` flow (`onboarding_start` -> `consent` -> `invite_code` -> `timezone` -> `done`).
- 18+ and Privacy Policy consent logged in `consent_log` (S9).
- Secure hashed invite code verification (`invite_codes` table).
- Hard limit of 100 users enforced for non-admins.
- Timezone prompt (default: Asia/Kolkata) with python `dateparser` logic.
- Admin bypass for invite gate.
- Bot entrypoint with `aiogram` 3.x long-polling
- PrivateChatOnlyMiddleware (ignores groups)
- RateLimitMiddleware (20 msg/min, 5 uploads/min)
- SqliteStorage for FSM (30 min state expiry)
- Main reply keyboard setup
- `/start`, `/help`, `/menu` handlers
- AdminOnly filter setup
- i18n basic JSON loader

### M0 — Foundation (done)
- Repository scaffolding: `pyproject.toml`, layout, `.env.example`
- `config.py`: typed config loader (env + file)
- `logging_setup.py`: structured logging with scrubbing filter (tokens, file names, doc text)
- `db/migrations/`: migration runner + full schema (Section 5 of blueprint)
- `security/crypto.py`: AES-256-GCM envelope encrypt/decrypt with round-trip tests
- `security/masking.py`: PII masking helpers
- `docs/ASSUMPTIONS.md`: decisions made where the blueprint was silent
