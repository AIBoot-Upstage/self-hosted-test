# Local Deployment Test

이 디렉터리는 MacBook/Tailscale staging 없이 현재 로컬 머신에서 배포 형태를 검증하기 위한 Compose 구성을 담는다.

## 실행

WSL 2에서 실행한다면 Docker Desktop의 WSL integration이 현재 distro에 켜져 있어야 한다.

```bash
./scripts/local-deploy-test.sh
```

기본 실행은 다음 값을 사용한다.

```text
AI_REVIEWER_IMAGE=ai-code-review-agent:local
PORT=8080
LLM_MODE=mock
PUBLISH_MODE=local
AI_REVIEWER_TOKEN=local-reviewer-token
GITHUB_WEBHOOK_SECRET=local-webhook-secret
```

스크립트는 image를 빌드하고, `api + postgres(pgvector)` stack을 실행한 뒤 health, policy sync, review smoke, webhook signature smoke를 확인한다.

## 확인

```bash
curl -fsS http://127.0.0.1:8080/healthz
docker compose -f infra/local-deploy/docker-compose.yml -p ai-code-review-agent-local ps
docker compose -f infra/local-deploy/docker-compose.yml -p ai-code-review-agent-local logs -f api
```

리뷰 결과와 로컬 comment markdown은 `.local-data/local-deploy` 아래에 저장된다.

## 중지

```bash
docker compose -f infra/local-deploy/docker-compose.yml -p ai-code-review-agent-local down
```

DB volume까지 제거하려면:

```bash
docker compose -f infra/local-deploy/docker-compose.yml -p ai-code-review-agent-local down -v
```

## 실제 GitHub App Webhook 테스트

로컬 API를 외부에서 접근 가능한 URL로 터널링한 뒤 GitHub App webhook URL을 다음처럼 설정한다.

```text
https://<tunnel-host>/v1/github/webhooks
```

그 다음 실제 webhook/comment 게시 모드로 로컬 stack을 실행한다.

```bash
LLM_MODE=litellm \
PUBLISH_MODE=github_app \
UPSTAGE_API_KEY=... \
UPSTAGE_API_BASE_URL=https://api.upstage.ai/v1 \
SOLAR3_MODEL=solar-pro3 \
GITHUB_WEBHOOK_SECRET=... \
GITHUB_APP_ID=... \
GITHUB_APP_PRIVATE_KEY="$(cat path/to/app-private-key.pem)" \
./scripts/local-deploy-test.sh
```

CI/check 결과를 기다리는 기본 모드는 다음 값이다.

```text
GITHUB_WEBHOOK_REVIEW_MODE=after_checks
```

CI가 없는 테스트 repository에서는 `GITHUB_WEBHOOK_REVIEW_MODE=pull_request`로 바꿔 PR 이벤트만으로 리뷰 실행을 확인할 수 있다.
