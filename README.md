# AI Code Review Agent

GitHub Pull Request의 diff, lint/test 결과, 저장소 정책을 분석해 상황별로
Solar3 low/medium/high 모델 경로를 선택하는 AI 코드 리뷰 에이전트입니다.

## Local Quickstart

의존성 설치 없이 mock LLM으로 핵심 흐름을 먼저 확인할 수 있습니다.

```bash
python3 -m backend.app.cli sample-data/review-request.json --sync-policies
python3 -m backend.app.cli sample-data/failing-review-request.json
```

실행 결과는 JSON으로 출력되고, 로컬 리뷰 댓글은 `.local-data/comments/`에
Markdown 파일로 저장됩니다.

## API Server

```bash
cp .env.example .env
docker compose up --build
```

Health check:

```bash
curl http://localhost:8080/healthz
```

Review request:

```bash
curl -X POST http://localhost:8080/v1/reviews \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer change-me" \
  --data @sample-data/review-request.json
```

## Model Modes

기본값은 로컬 개발용 mock 모드입니다.

```text
LLM_MODE=mock
PUBLISH_MODE=local
```

LiteLLM과 Upstage Solar3 API를 사용하려면 `.env`에서 다음 값을 설정합니다.
실제 LiteLLM model id는 사용하는 Upstage/LiteLLM 계정 설정에 맞게 바꿀 수
있도록 환경 변수로 분리했습니다.

```text
LLM_MODE=litellm
UPSTAGE_API_KEY=...
SOLAR3_LOW_MODEL=...
SOLAR3_MEDIUM_MODEL=...
SOLAR3_HIGH_MODEL=...
```

GitHub에 댓글을 게시하려면 다음 값을 설정합니다.

```text
PUBLISH_MODE=github
GITHUB_TOKEN=...
```

## Routing Rules

| Condition | Route | Model tier |
| --- | --- | --- |
| syntax, lint, or test failed | `simple_failure_review` | `solar3-low` |
| checks passed and repository policy exists | `policy_context_review` | `solar3-medium` |
| high-risk paths, large diff, or low confidence | `deep_quality_review` | `solar3-high` |

## Repository Policies

로컬 MVP에서는 `POLICY_ROOT` 아래 Markdown 파일을 간단한 keyword retrieval로
검색합니다. 향후 GCP 배포 단계에서는 같은 `LocalPolicyIndex.search()` 계약을
pgvector 기반 검색 서비스로 교체하면 됩니다.

기본 샘플:

```text
policies/sample-review-policy.md
```

## GitHub Action Integration

다른 저장소에서 `github-action/request_ai_review.py`를 실행하면 PR 파일 목록,
lint/test 결과를 모아 `/v1/reviews`로 전송합니다.

필요한 secrets:

```text
AI_REVIEWER_API_URL=https://<cloud-run-url>
AI_REVIEWER_TOKEN=<server-token>
```

## GCP Deployment Direction

이 저장소는 Cloud Run 배포를 기준으로 구성되어 있습니다.

1. `Dockerfile`로 API image를 빌드합니다.
2. GitHub Actions CI에서 ruff와 test를 실행합니다.
3. 수동 CD workflow가 Artifact Registry에 image를 push합니다.
4. Cloud Run 서비스로 배포합니다.

배포 workflow는 `.github/workflows/deploy-gcp-cloud-run.yml`에 있습니다. GCP 배포는 staging 검증 이후 수동 실행하도록 구성되어 있습니다.

## MacBook Staging Deployment

실제 서버에 배포하기 전에 Tailscale로 연결된 MacBook을 staging 서버로 사용할 수 있습니다.

배포 workflow:

```text
.github/workflows/deploy-macbook-staging.yml
```

흐름:

1. GitHub Actions에서 ruff, pytest, local smoke review를 실행합니다.
2. `tailscale/github-action@v4`로 GitHub runner를 tailnet에 연결합니다.
3. SSH/SCP로 현재 revision을 MacBook에 업로드합니다.
4. MacBook에서 `docker compose up --build -d`를 실행합니다.
5. `/healthz`와 `/v1/reviews` smoke test를 실행합니다.

기본 설정은 Tailscale `TS_AUTHKEY` secret 방식이며, MacBook host/user도 GitHub Secrets에서 읽습니다. `MACBOOK_SSH_KEY` secret에는 SSH private key 원문이 아니라 base64 인코딩된 private key를 넣습니다.

자세한 준비 절차는 [infra/macbook-staging/README.md](infra/macbook-staging/README.md)를 참고하세요.

## Project Layout

```text
backend/app/core        domain models, routing, security
backend/app/services    RAG, prompt, LLM, publisher, orchestrator
backend/app/storage     local JSON store
github-action           PR collector script for GitHub Actions
policies                sample repository review policies
sample-data             local review request payloads
infra/gcp               GCP deployment notes and sample env
infra/macbook-staging   Tailscale MacBook staging deployment guide
```
