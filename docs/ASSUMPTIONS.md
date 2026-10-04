# SQUARES Vault – Assumptions Log

Every decision made where the Architecture Blueprint was silent is recorded here with a one-line reason.
This file is the source of truth for design choices not explicitly locked in the blueprint.

| # | Module | Decision | Reason |
|---|--------|----------|--------|
| A1 | `db/connection.py` | Single shared `aiosqlite.Connection` (not per-request) | SQLite has no concurrency benefit from multiple connections; WAL serializes writes; simpler than pooling |
| A2 | `db/connection.py` | `PRAGMA synchronous = NORMAL` (not FULL) | WAL mode + NORMAL gives durability on crash without fsync on every write; good balance for a single-VM deployment |
| A3 | `db/connection.py` | `PRAGMA temp_store = MEMORY` | Avoids temp files on the shared `/tmp`; harmless for our query sizes |
| A4 | `logging_setup.py` | Log scrubber also redacts strings ≥40 alphanum chars | Catches unknown long tokens; may over-redact ULIDs in some log contexts; the record_id field is safe to log by name |
| A5 | `logging_setup.py` | `json_logs=True` default; dev can set `False` | Production needs machine-parseable lines; dev benefits from colour |
| A6 | `security/masking.py` | Vehicle mask: first 4 + last 2 visible | Reveals state code + series (not sensitive) but hides registration number middle; enough to identify the vehicle category |
| A7 | `db/migrations/` | Migrations are plain Python files, not SQL files | Allows async `up()` functions; easier to add Python logic in future migrations (e.g. data migration) |
| A8 | `pyproject.toml` | `aioboto3>=13` added alongside `boto3` | `aioboto3` wraps boto3 for async use; needed for the async backup upload path |
| A9 | `pyproject.toml` | `zstandard` added | Fast compression for backups before `age` encryption |
| A10 | `security/crypto.py` | AAD constants `sv-dek-v1` and `sv-ct-v1` used for domain separation | Prevents a ciphertext blob from one role being used as another's input even if master key is reused |
