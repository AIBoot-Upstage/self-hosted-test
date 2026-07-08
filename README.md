# AI Code Review Agent

GitHub Pull Request의 diff, lint/test 결과, 저장소 정책을 분석해 상황별로
동일한 Solar3 모델의 low/medium/high 추론 강도를 선택하는 AI 코드 리뷰 에이전트입니다.

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

Docker/배포 기본값은 LiteLLM을 통해 Solar3 모델을 호출하는 모드입니다.
오프라인 개발이나 CI smoke test에서는 `LLM_MODE=mock`으로 deterministic reviewer를
사용할 수 있습니다.

```text
LLM_MODE=litellm
UPSTAGE_API_KEY=...
PUBLISH_MODE=local
```

실제 LiteLLM model id는 하나의 `SOLAR3_MODEL`로 관리하고, 라우팅 결과에 따라
`reasoning_effort`만 low/medium/high로 다르게 전달합니다.

```text
SOLAR3_MODEL=...
SOLAR3_LOW_REASONING_EFFORT=low
SOLAR3_MEDIUM_REASONING_EFFORT=medium
SOLAR3_HIGH_REASONING_EFFORT=high
```

GitHub에 댓글을 게시하려면 다음 값을 설정합니다.

```text
PUBLISH_MODE=github
GITHUB_TOKEN=...
```

## Routing Rules

| Condition | Route | Review tier / reasoning effort |
| --- | --- | --- |
| syntax, lint, or test failed | `simple_failure_review` | `solar3-low` / `low` |
| checks passed and repository policy exists | `policy_context_review` | `solar3-medium` / `medium` |
| high-risk paths, large diff, or low confidence | `deep_quality_review` | `solar3-high` / `high` |

## Repository Policies

Docker/배포 기본값은 Postgres + pgvector에 정책 chunk를 동기화한 뒤 검색합니다.
`POLICY_ROOT` 아래 Markdown, CODEOWNERS, PR template을 policy chunk로 분리해
`policy_chunks` 테이블에 저장합니다. DB가 없는 로컬 개발에서는 같은 인터페이스로
파일 기반 keyword retrieval fallback을 사용할 수 있습니다.

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
2. GitHub Actions에서 Docker image를 빌드해 GHCR에 push합니다.
3. `tailscale/github-action@v4`로 GitHub runner를 tailnet에 연결합니다.
4. SSH/SCP로 `.env`와 staging compose 파일만 MacBook에 업로드합니다.
5. MacBook에서 GHCR image를 pull하고 `api + postgres(pgvector)` compose stack을 실행합니다.
6. `/healthz`와 `/v1/reviews` smoke test를 실행합니다.

기본 설정은 Tailscale `TS_AUTHKEY` secret 방식이며, MacBook host/user도 GitHub Secrets에서 읽습니다. `MACBOOK_SSH_KEY` secret에는 SSH private key 원문 또는 base64 인코딩된 private key를 넣습니다. 줄바꿈 문제를 피하려면 base64 방식을 권장합니다.

GHCR image push/pull은 기본적으로 workflow `GITHUB_TOKEN`을 사용합니다. 조직 정책 때문에 package write 권한을 받을 수 없으면 `GHCR_TOKEN` secret과 `GHCR_USER` variable을 설정해 PAT 기반으로 배포할 수 있습니다.

자세한 준비 절차는 [infra/macbook-staging/README.md](infra/macbook-staging/README.md)를 참고하세요.

## Project Layout

```text
backend/app/core        domain models, routing, security
backend/app/services    RAG, prompt, LLM, publisher, orchestrator
backend/app/storage     local JSON and PostgreSQL review stores
github-action           PR collector script for GitHub Actions
policies                sample repository review policies
sample-data             local review request payloads
infra/gcp               GCP deployment notes and sample env
infra/macbook-staging   Tailscale MacBook staging deployment guide
```
