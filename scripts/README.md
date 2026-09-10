# LinguaFlow operational scripts

Scripts are grouped by impact so operators can identify risk before execution.
Run them from the repository root unless a script explicitly states otherwise.

## Layout

```text
scripts/
├── development/   Local setup, launchers, and sample-data seeds
├── maintenance/   Data maintenance, backfills, migrations, and reports
└── deployment/    Provisioning, release, verification, backup, and rollback
```

## Development

| Script | Purpose | Example |
|---|---|---|
| `development/setup.sh` | Create a virtual environment and install dependencies | `./scripts/development/setup.sh` |
| `development/dev.sh` | Start or stop backend and frontend with Bash | `./scripts/development/dev.sh` |
| `development/dev.ps1` | Start backend and frontend with PowerShell | `.\scripts\development\dev.ps1` |
| `development/seed_dev_users.py` | Create local-only sample accounts | `python scripts/development/seed_dev_users.py` |
| `development/seed_glossary.py` | Idempotently import the initial glossary | `python scripts/development/seed_glossary.py` |

## Maintenance

| Script | Impact |
|---|---|
| `maintenance/report_metrics.py` | Read-only database analysis and local reports |
| `maintenance/backfill_assistant_chunks.py` | Rebuild assistant indexes; may consume embedding quota |
| `maintenance/mine_glossary.py` | Analyze correction logs and optionally write proposals |
| `maintenance/migrate_attachments_to_supabase.py` | Copy attachments; deletes local files only with explicit confirmation |

Use `--dry-run` or `--no-write` whenever supported. Confirm data scope before a
write operation. Resume state and generated reports belong in ignored runtime directories.

## Deployment

```text
run_remote_release.sh
        ↓
production_release_wrapper.sh
        ↓
backup_postgres.sh → deploy_release.sh → verify_production.sh
                                             ↓
                                     finalize_release.sh
```

- `configure_vps_env.sh` is intended for first-time VPS provisioning.
- `deploy_release.sh` creates a backup before migrations and does not
  automatically roll back the database.
- `finalize_release.sh` updates the last-known-good revision only after public
  verification and image-identity checks succeed.
- Never run deployment scripts with a development environment file.

See the [deployment runbook](../docs/operations/deployment.md) for server setup.

## Conventions

- Shell scripts use POSIX `sh`, except launchers that explicitly require Bash.
- Python scripts work from the repository root without relying on the caller's directory.
- Data-changing scripts document idempotency, recovery, and dry-run behavior.
- Never write secrets, tokens, or environment-file contents to logs.
- Temporary files and runtime reports belong in ignored runtime directories.
