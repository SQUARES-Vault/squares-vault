# SQUARES Vault

A Telegram bot for personal life-admin: vehicle papers, warranties, bills, insurance, IDs, birthdays, health docs, and home contracts — stored in **your own** Google Drive, reminded at the right time, retrieved on demand.

## Quick start (development)

```bash
python -m venv .venv && source .venv/bin/activate   # Linux/macOS
# or: .venv\Scripts\Activate.ps1                    # Windows
pip install -e ".[dev]"
cp .env.example .env    # fill in your secrets
pytest
```

## Production deploy

See [`deploy/install.sh`](deploy/install.sh) and [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## Architecture

See [`SQUARES_Vault_Architecture_Blueprint.md`](../SQUARES_Vault_Architecture_Blueprint.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).
