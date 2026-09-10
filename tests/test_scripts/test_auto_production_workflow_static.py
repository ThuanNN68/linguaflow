"""Static safety contract for the automatic production control plane."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
TRANSPORT = (ROOT / "scripts/deployment/run_remote_release.sh").read_text(encoding="utf-8")
WRAPPER = (ROOT / "scripts/deployment/production_release_wrapper.sh").read_text(
    encoding="utf-8"
)
DEPLOY_RELEASE = (ROOT / "scripts/deployment/deploy_release.sh").read_text(encoding="utf-8")


def _job(name: str, next_name: str | None = None) -> str:
    start = WORKFLOW.index(f"  {name}:")
    if next_name is None:
        return WORKFLOW[start:]
    return WORKFLOW[start : WORKFLOW.index(f"  {next_name}:", start + 1)]


def test_existing_ci_gate_remains_postgres_pgvector_and_full_suite() -> None:
    lint = _job("lint-and-test", "production-gate")
    # Public CI must run on a GitHub-hosted runner; only the jobs that reach the
    # private production host are allowed to require self-hosted infrastructure.
    assert "runs-on: ubuntu-latest" in lint
    assert "image: pgvector/pgvector:pg16" in lint
    assert "pip install -r requirements.txt" in lint
    assert "ruff check src/ tests/ eval/" in lint
    assert "pytest tests/ -v --tb=short" in lint
    assert "group: ci-verification-${{ github.ref }}" in lint
    assert "cancel-in-progress: true" in lint


def test_ci_gate_verifies_frontend_before_production_jobs() -> None:
    lint = _job("lint-and-test", "production-gate")
    assert "actions/setup-node@v4" in lint
    assert "cache-dependency-path: frontend/package-lock.json" in lint
    assert "working-directory: frontend" in lint
    assert "npm ci" in lint
    assert "npm test" in lint
    assert "npx tsc --noEmit" in lint
    assert "npm run lint" in lint
    assert "npm run build" in lint


def test_pull_request_events_can_only_run_lint_and_test() -> None:
    assert "pull_request:\n    branches: [main, develop_v2]" in WORKFLOW
    gate = _job("production-gate", "build-production-artifacts")
    rehearsal = _job("production-channel-rehearsal")
    assert "github.event_name == 'push'" in gate
    assert "github.ref == 'refs/heads/develop_v2'" in gate
    assert "needs: lint-and-test" in gate
    assert "needs.lint-and-test.result == 'success'" in gate
    assert "github.event_name == 'push'" in rehearsal
    assert "github.ref == 'refs/heads/develop_v2'" in rehearsal


def test_gate_uses_exact_event_sha_and_merged_pr_evidence() -> None:
    gate = _job("production-gate", "build-production-artifacts")
    assert "RELEASE_SHA: ${{ github.sha }}" in gate
    assert "listPullRequestsAssociatedWithCommit" in gate
    assert 'pr.base?.ref === "develop_v2"' in gate
    assert "pr.merge_commit_sha === sha" in gate
    assert "Fail closed:" in gate


def test_artifacts_are_exact_sha_and_never_latest() -> None:
    build = _job("build-production-artifacts", "deploy-production")
    assert "needs.production-gate.outputs.release_sha" in build
    assert "org.opencontainers.image.revision=" in build
    assert "backend_digest" in build
    assert "frontend_digest" in build
    assert "PARTIAL RELEASE STATE" in build
    assert ":latest" not in WORKFLOW


def test_production_jobs_are_serialized_without_cancellation() -> None:
    deploy = _job("deploy-production", "production-channel-rehearsal")
    rehearsal = _job("production-channel-rehearsal")
    for job in (deploy, rehearsal):
        assert "group: linguaflow-production" in job
        assert "cancel-in-progress: false" in job
        assert "queue: max" in job
        assert "runs-on: self-hosted" in job


def test_deploy_refuses_a_sha_superseded_by_newer_develop_v2_head() -> None:
    deploy = _job("deploy-production", "production-channel-rehearsal")
    assert "github.rest.repos.getBranch" in deploy
    assert 'branch: "develop_v2"' in deploy
    assert "branch.data.commit.sha === process.env.RELEASE_SHA" in deploy
    assert "if: steps.freshness.outputs.current == 'true'" in deploy


def test_deploy_job_has_only_channel_secret_and_minimum_permissions() -> None:
    deploy = _job("deploy-production", "production-channel-rehearsal")
    assert "packages: read" in deploy
    assert "contents: read" in deploy
    assert "PROD_SSH_PRIVATE_KEY: ${{ secrets.PROD_SSH_PRIVATE_KEY }}" in deploy
    assert "GHCR_TOKEN: ${{ github.token }}" in deploy
    for runtime_secret in (
        "JWT_SECRET",
        "DATABASE_URL",
        "POSTGRES_PASSWORD",
        "SMTP_PASSWORD",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
    ):
        assert runtime_secret not in deploy


def test_ssh_transport_pins_host_and_keeps_token_out_of_arguments() -> None:
    assert "StrictHostKeyChecking=yes" in TRANSPORT
    assert "UserKnownHostsFile=" in TRANSPORT
    assert "StrictHostKeyChecking=no" not in TRANSPORT
    assert "printf '%s\\n' \"$GHCR_TOKEN\" | ssh" in TRANSPORT
    sudo_line = next(line for line in TRANSPORT.splitlines() if "sudo -n" in line)
    assert "GHCR_TOKEN" not in sudo_line


def test_transport_writes_a_platform_independent_checksum_manifest() -> None:
    assert 'checksum=$(sha256sum "$relative" | awk \'{print $1}\')' in TRANSPORT
    assert "printf '%s  %s\\n' \"$checksum\" \"$relative\" >>SHA256SUMS" in TRANSPORT


def test_transport_exports_bundle_from_the_checked_out_git_object() -> None:
    assert 'if [ -n "${RELEASE_BUNDLE_ROOT-}" ]' in TRANSPORT
    assert 'cd -- "$RELEASE_BUNDLE_ROOT"' in TRANSPORT
    assert 'git -C "$repo_root" cat-file -e "HEAD:$source"' in TRANSPORT
    assert 'git -C "$repo_root" show "HEAD:$source" >"$destination"' in TRANSPORT
    assert 'cp "$repo_root/$source" "$destination"' not in TRANSPORT


def test_root_wrapper_verifies_before_mutating_and_preserves_durable_state() -> None:
    digest_check = WRAPPER.index('verify_image "$BACKEND_REPOSITORY"')
    deploy_call = WRAPPER.index('sh scripts/deployment/deploy_release.sh "$release_sha"')
    assert digest_check < deploy_call
    assert "OCI revision does not match" in WRAPPER
    assert "root-owned bundle copy mismatch" in WRAPPER
    assert "image ID does not match the expected digest" in WRAPPER
    assert "PostgreSQL container identity changed" in WRAPPER
    assert "durable volume identity changed" in WRAPPER
    assert "LINGUAFLOW_LKG_FILE=$LKG_FILE sh scripts/deployment/finalize_release.sh" in WRAPPER
    assert "docker login ghcr.io" in WRAPPER
    assert "--password-stdin" in WRAPPER
    assert "docker logout ghcr.io" in WRAPPER
    assert "DOCKER_CONFIG=$docker_config" in WRAPPER


def test_no_automatic_destructive_or_data_recovery_commands() -> None:
    combined = TRANSPORT + WRAPPER
    prohibited = (
        "docker compose down",
        "docker volume rm",
        "docker system prune",
        "docker image prune",
        "alembic downgrade",
        "pg_restore",
        "scripts/seed_",
    )
    for command in prohibited:
        assert command not in combined


def test_rollout_keeps_single_app_replicas_and_does_not_target_postgres() -> None:
    rollout = """if ! compose up -d --no-build --force-recreate \\
  --scale backend=1 --scale frontend=1 --scale caddy=1 \\
  backend frontend caddy; then"""
    assert rollout in DEPLOY_RELEASE
    assert "backend frontend caddy postgres" not in DEPLOY_RELEASE


def test_rehearsal_requires_existing_lkg_and_preserves_container_ids() -> None:
    assert '[ "$release_sha" = "$current_lkg" ]' in WRAPPER
    assert "rehearsal changed backend container identity" in WRAPPER
    assert "rehearsal changed frontend container identity" in WRAPPER
    assert "rehearsal changed PostgreSQL container identity" in WRAPPER
    assert "rehearsal changed Caddy container identity" in WRAPPER
    assert "ALREADY_DEPLOYED|mode=rehearse" in WRAPPER
