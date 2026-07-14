# GCP Deployment Notes

Target runtime: GCE VM self-host (`ai-code-review-agent`, `asia-northeast3-c`).

`docker-compose.yml`이 `api + postgres(pgvector)`를 기본 세트로 실행하도록 구성되어 있다.
VM에서는 `COMPOSE_PROFILES=edge`로 `caddy` reverse proxy/TLS 서비스까지 함께 실행한다.
GitHub Actions가 빌드한 Docker image를 `docker load`로 올리고, compose는 `--no-build`로
image만 실행한다. 별도의 managed Postgres(Cloud SQL)는 필요하지 않다.

현재 GCP 배포 workflow는 main 브랜치 CI 성공 후 자동 실행되며, `workflow_dispatch`로도
수동 실행할 수 있다. GitHub repository variable `CD_DEPLOY_TARGET=local-only`로 바꾸면
main push 후 image build 검증만 하고 VM 배포는 건너뛴다.

Required GCP resources:

1. GCE VM (`ai-code-review-agent`, Ubuntu 22.04, `docker-ce` + `docker-compose-plugin`
   설치됨)
2. 정적 외부 IP (`gcloud compute addresses create`로 예약 후 VM에 연결) — GitHub App
   webhook과 CI/CD가 참조할 안정적인 주소가 필요하다.
3. 방화벽 규칙: `80`, `443`만 외부 공개, app 컨테이너 `PORT`는 loopback/internal 전용,
   `22`는 IAP 대역(`35.235.240.0/20`)으로만 허용
4. VM 위에서 80/443을 받아 내부 app `PORT`로 넘기는 `caddy` reverse proxy + TLS 종료
   (자동 Let's Encrypt 인증서 발급). `DOMAIN`이 실제 정적 IP를 가리키는 도메인이어야 한다.
   실제 도메인이 없다면 정적 IP를 가리키는 `sslip.io` 같은 무료 서비스를 쓸 수 있다
   (예: 정적 IP `34.64.123.45` -> `34-64-123-45.sslip.io`). Let's Encrypt는 IP 주소가
   아닌 도메인 이름 앞으로만 인증서를 발급하므로, `DOMAIN`이 실제로 정적 IP를 가리키게
   DNS(A 레코드 또는 sslip.io 규칙)가 맞춰져 있어야 한다.
5. Secret Manager 또는 VM의 `.env` 파일로 관리되는 `AI_REVIEWER_TOKEN`,
   `UPSTAGE_API_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY`,
   `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`
6. Workload Identity Federation provider (GitHub Actions가 IAP 터널로 VM에 배포 명령을
   보내기 위한 인증)
7. GitHub App 설치 대상 repository/organization

Required GCP APIs:

```text
iamcredentials.googleapis.com
sts.googleapis.com
compute.googleapis.com
iap.googleapis.com
oslogin.googleapis.com
```

`gcloud compute scp` 또는 `gcloud compute ssh`에서
`Unable to acquire impersonated credentials`와
`IAM Service Account Credentials API ... is disabled`가 나오면 다음 API가 꺼진 상태다.

```bash
gcloud services enable \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  compute.googleapis.com \
  iap.googleapis.com \
  oslogin.googleapis.com \
  --project <PROJECT_ID>
```

Recommended GitHub repository variables:

```text
GCP_PROJECT_ID=charged-curve-501705-n9
GCP_ZONE=asia-northeast3-c
GCE_INSTANCE=ai-code-review-agent
AI_REVIEWER_IMAGE_NAME=ai-code-review-agent-api
CD_DEPLOY_TARGET=gcp-vm
```

`GITHUB_APP_ID`, `GITHUB_WEBHOOK_REVIEW_MODE`, `UPSTAGE_API_KEY`,
`GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY` 등 런타임 값은 GitHub Actions
variables/secrets가 아니라 VM의 `/opt/ai-code-review-agent-deploy/.env`에서 관리한다.

Recommended GitHub repository secrets:

```text
GCP_WORKLOAD_IDENTITY_PROVIDER=projects/.../providers/...
GCP_SERVICE_ACCOUNT=github-deployer@<project-id>.iam.gserviceaccount.com
GCP_DEPLOY_SSH_PRIVATE_KEY=<fixed OpenSSH private key content>
```

`GCP_DEPLOY_SSH_PRIVATE_KEY`는 `gcloud compute scp`/`ssh`가 매 실행마다 임시 키를 새로
발급하고 OS Login 전파를 기다리는 지연을 없애기 위한 고정 키다. 자세한 생성/등록 방법은
[`server-deployment-settings.md`](./server-deployment-settings.md)를 참고한다.

Required IAM roles for the deployer service account:

```text
roles/iap.tunnelResourceAccessor
roles/compute.viewer
roles/compute.osAdminLogin
roles/iam.serviceAccountUser on the VM attached service account
```

`roles/compute.osAdminLogin`이 필요한 이유: 배포 SA의 OS Login 계정은 세션마다
`sa_<unique-id>` 형태로 임시 생성되고 `docker` group 멤버십을 세션 간에 유지하지
못한다. 그래서 배포 스크립트는 `/opt/ai-code-review-agent-deploy` 접근과 `docker`
명령을 전부 `sudo`로 실행하며, 이건 `compute.osLogin`(sudo 불가)이 아니라
`compute.osAdminLogin`(passwordless sudo, `google-sudoers` group)이어야만 동작한다.

`gcloud compute scp`에서 `The user does not have access to service account
'<PROJECT_NUMBER>-compute@developer.gserviceaccount.com'`가 나오면, GitHub Actions가
impersonate하는 배포용 service account에 VM service account 사용 권한이 없는 상태다.

```bash
PROJECT_ID=charged-curve-501705-n9
PROJECT_NUMBER=1026819034842
DEPLOYER_SA=github-deployer@${PROJECT_ID}.iam.gserviceaccount.com
VM_ATTACHED_SA=${PROJECT_NUMBER}-compute@developer.gserviceaccount.com

gcloud iam service-accounts add-iam-policy-binding "${VM_ATTACHED_SA}" \
  --project "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOYER_SA}" \
  --role "roles/iam.serviceAccountUser"
```

VM의 `/opt/ai-code-review-agent-deploy/.env`에는 image build용 local override를 넣지 않는다.
로컬 테스트에서는 `COMPOSE_FILE=docker-compose.yml:docker-compose.local.yml`, VM 배포에서는
`COMPOSE_FILE=docker-compose.yml`만 사용한다. Caddy를 켜려면 `COMPOSE_PROFILES=edge`와
`DOMAIN=<domain>`을 설정한다. 배포 workflow는 실행할 image tag를 `AI_REVIEWER_IMAGE`
환경변수로 주입한다.

GitHub App setup:

1. Register a GitHub App owned by the target organization or user.
2. Set webhook URL to `https://<DOMAIN>/v1/github/webhooks` (the same `DOMAIN` value
   configured for Caddy — a bare IP will not have a valid HTTPS cert).
3. Generate a high-entropy webhook secret and save it as `GITHUB_WEBHOOK_SECRET`.
4. Generate a private key and save the PEM content as `GITHUB_APP_PRIVATE_KEY`.
5. Configure repository permissions:
   - `Contents: Read`
   - `Pull requests: Read and write`
   - `Checks: Read and write`
   - `Metadata: Read`
6. Subscribe to events:
   - `pull_request`
   - `check_suite`
   - `check_run`
   - `installation`
   - `installation_repositories`

With `GITHUB_WEBHOOK_REVIEW_MODE=after_checks`, review execution starts from
`check_suite.completed`. Use `pull_request` mode only for repositories without CI.

## Langfuse

Langfuse Cloud(무료 티어)를 사용한다. VM의 `.env`에 `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`(기본값 `https://cloud.langfuse.com`)를 설정하면
모든 LiteLLM 호출이 자동으로 기록된다.
