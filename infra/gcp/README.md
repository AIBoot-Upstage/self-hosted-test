# GCP Deployment Notes

Target runtime: GCE VM self-host (`ai-code-review-agent`, `asia-northeast3-c`).

`docker-compose.yml`이 `api + postgres(pgvector)`를 한 세트로 실행하도록 이미 구성되어
있으므로, 이 compose 파일을 VM에 그대로 배포한다. 별도의 managed Postgres(Cloud SQL)는
필요하지 않다.

현재 GCP 배포 workflow는 `workflow_dispatch` 수동 실행 전용이다. 로컬 배포 테스트
(`./scripts/local-deploy-test.sh`)가 끝난 뒤 GitHub Actions에서
`Deploy to GCP VM`을 직접 실행한다.

Required GCP resources:

1. GCE VM (`ai-code-review-agent`, Ubuntu 22.04, `docker-ce` + `docker-compose-plugin`
   설치됨)
2. 정적 외부 IP (`gcloud compute addresses create`로 예약 후 VM에 연결) — GitHub App
   webhook과 CI/CD가 참조할 안정적인 주소가 필요하다.
3. 방화벽 규칙: `80`, `443`만 외부 공개, `8000`(app 컨테이너)은 내부 전용, `22`는 IAP
   대역(`35.235.240.0/20`)으로만 허용
4. VM 위에서 80/443을 받아 내부 8000으로 넘기는 리버스 프록시 + TLS 종료
   (Caddy 권장 — 자동 Let's Encrypt 인증서 발급)
5. Secret Manager 또는 VM의 `.env` 파일로 관리되는 `AI_REVIEWER_TOKEN`,
   `UPSTAGE_API_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY`,
   `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`
6. Workload Identity Federation provider (GitHub Actions가 IAP 터널로 VM에 배포 명령을
   보내기 위한 인증)
7. GitHub App 설치 대상 repository/organization

Recommended GitHub repository variables:

```text
GCP_PROJECT_ID=charged-curve-501705-n9
GCP_ZONE=asia-northeast3-c
GCE_INSTANCE=ai-code-review-agent
```

`GITHUB_APP_ID`, `GITHUB_WEBHOOK_REVIEW_MODE`, `UPSTAGE_API_KEY`,
`GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY` 등 런타임 값은 GitHub Actions
variables/secrets가 아니라 VM의 `~/ai-code-review-agent-deploy/.env`에서 관리한다.

Recommended GitHub repository secrets:

```text
GCP_WORKLOAD_IDENTITY_PROVIDER=projects/.../providers/...
GCP_SERVICE_ACCOUNT=github-deployer@<project-id>.iam.gserviceaccount.com
```

GitHub App setup:

1. Register a GitHub App owned by the target organization or user.
2. Set webhook URL to `https://<static-ip-or-domain>/v1/github/webhooks`.
3. Generate a high-entropy webhook secret and save it as `GITHUB_WEBHOOK_SECRET`.
4. Generate a private key and save the PEM content as `GITHUB_APP_PRIVATE_KEY`.
5. Configure repository permissions:
   - `Contents: Read`
   - `Pull requests: Read and write`
   - `Checks: Read`
   - `Metadata: Read`
6. Subscribe to events:
   - `pull_request`
   - `check_suite`
   - `installation`
   - `installation_repositories`

With `GITHUB_WEBHOOK_REVIEW_MODE=after_checks`, review execution starts from
`check_suite.completed`. Use `pull_request` mode only for repositories without CI.

## Langfuse

Langfuse Cloud(무료 티어)를 사용한다. VM의 `.env`에 `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`(기본값 `https://cloud.langfuse.com`)를 설정하면
모든 LiteLLM 호출이 자동으로 기록된다.
