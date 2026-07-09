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

기본 응답은 `202 Accepted`와 `review_run_id`이며, 리뷰는 background에서 실행됩니다.
진행 상황은 SSE로 구독할 수 있습니다.

```bash
curl -N http://localhost:8080/v1/reviews/<review_run_id>/events \
  -H "Authorization: Bearer change-me"
```

동기식 smoke test가 필요하면 `wait=true`를 사용합니다.

```bash
curl -X POST "http://localhost:8080/v1/reviews?wait=true" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer change-me" \
  --data @sample-data/review-request.json
```

리뷰 이력은 목록으로 조회할 수 있습니다(`limit`, `route_name`, `model_tier`로 필터링
가능, 기본 `limit=100`).

```bash
curl "http://localhost:8080/v1/reviews?limit=20" \
  -H "Authorization: Bearer change-me"
```

토큰/비용/latency/verdict별 통계 조회는 Langfuse Cloud 대시보드([Model Modes](#model-modes)
참고)에서 확인합니다.

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

GitHub에 댓글을 게시하려면 PAT 기반 또는 GitHub App 기반 중 하나를 선택합니다.

```text
PUBLISH_MODE=github
GITHUB_TOKEN=...
```

서비스형 webhook 배포에서는 GitHub App 방식을 사용합니다.

```text
PUBLISH_MODE=github_app
GITHUB_WEBHOOK_SECRET=...
GITHUB_WEBHOOK_REVIEW_MODE=after_checks
GITHUB_APP_ID=...
GITHUB_APP_PRIVATE_KEY=...
```

### Langfuse

`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`를 설정하면 `LLM_MODE=litellm`에서 실행되는
모든 LiteLLM 호출(모델, 토큰, `cost_usd`, latency, `review_run_id`/route/verdict
metadata)이 Langfuse Cloud에 자동 기록됩니다. 키를 비워두면 Langfuse 연동은
비활성화되고 기존 동작과 동일합니다.

```text
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com
```

## Routing Rules

| Condition | Route | Review tier / reasoning effort |
| --- | --- | --- |
| syntax, lint, or test failed | `simple_failure_review` | `solar3-low` / `low` |
| checks passed and repository policy exists | `policy_context_review` | `solar3-medium` / `medium` |
| high-risk paths, large diff, or low confidence | `deep_quality_review` | `solar3-high` / `high` |

## LangGraph Workflow

리뷰 실행 파이프라인은 LangGraph `StateGraph`로 구성됩니다.

```text
create_review
 -> extract_features
 -> select_route
 -> retrieve_policies 또는 skip_policy_retrieval
 -> build_prompt
 -> call_llm
 -> assemble_result
 -> persist_result
 -> publish_comment
 -> complete_review
```

각 node는 SSE 이벤트를 발행하므로 리뷰 실행 상태를 단계별로 추적할 수 있습니다.
로컬 가상환경에 LangGraph가 설치되어 있지 않은 경우 테스트 편의를 위해 같은 node/edge
순서를 따르는 fallback executor가 동작하지만, 배포 환경은 `pyproject.toml`의
`langgraph` 의존성으로 실제 LangGraph 런타임을 사용합니다.

## Repository Policies

Docker/배포 기본값은 Postgres + pgvector에 정책 chunk를 동기화한 뒤 검색합니다.
`POLICY_ROOT` 아래 Markdown, CODEOWNERS, PR template을 policy chunk로 분리해
`policy_chunks` 테이블에 저장합니다. DB가 없는 로컬 개발에서는 같은 인터페이스로
파일 기반 keyword retrieval fallback을 사용할 수 있습니다.

기본 샘플:

```text
policies/sample-review-policy.md
```

## GitHub App Webhook Integration

기본 제품 방향은 GitHub App 설치형 리뷰 서비스입니다. 대상 repository 또는
organization에 GitHub App을 설치하고 webhook URL을 다음으로 설정합니다.

```text
https://<vm-static-ip-or-domain>/v1/github/webhooks
```

권장 GitHub App 권한:

```text
Contents: Read
Pull requests: Read and write
Checks: Read
Metadata: Read
```

권장 구독 이벤트:

```text
pull_request
check_suite
installation
installation_repositories
```

`GITHUB_WEBHOOK_REVIEW_MODE=after_checks`에서는 `pull_request` 이벤트는 수신만 하고,
`check_suite.completed` 이후 GitHub API로 diff/check 결과를 수집해 리뷰를 실행합니다.
CI가 없는 데모 repository는 `GITHUB_WEBHOOK_REVIEW_MODE=pull_request`로 바꾸면 PR 이벤트
만으로 리뷰를 실행할 수 있습니다.

기존 `/v1/reviews` 엔드포인트는 내부 실행 API와 smoke test 용도로 유지합니다. 다른
저장소에서 `github-action/request_ai_review.py`를 실행하는 GitHub Actions 방식도 수동
fallback으로 사용할 수 있습니다.

Actions fallback에 필요한 secrets:

```text
AI_REVIEWER_API_URL=https://<vm-static-ip-or-domain>
AI_REVIEWER_TOKEN=<server-token>
```

## Local Deployment Test

실제 서버에 올리기 전에 현재 로컬 환경에서 image 기반 배포 흐름을 검증할 수 있습니다.
이 경로는 GHCR, SSH, Tailscale을 사용하지 않습니다.
WSL 2에서 실행한다면 Docker Desktop의 WSL integration이 현재 distro에 켜져 있어야 합니다.

```bash
./scripts/local-deploy-test.sh
```

스크립트가 수행하는 일:

1. `Dockerfile`로 `ai-code-review-agent:local` image를 빌드합니다.
2. `infra/local-deploy/docker-compose.yml`로 `api + postgres(pgvector)` stack을 실행합니다.
3. `/healthz`를 확인합니다.
4. repository policy sync를 실행합니다.
5. `/v1/reviews?wait=true`로 동기식 리뷰 smoke test를 실행합니다.
6. `/v1/github/webhooks`에 서명된 `ping` webhook을 보내 signature 검증을 확인합니다.

기본값은 안전한 로컬 검증용입니다.

```text
LLM_MODE=mock
PUBLISH_MODE=local
AI_REVIEWER_TOKEN=local-reviewer-token
GITHUB_WEBHOOK_SECRET=local-webhook-secret
```

실제 GitHub App webhook을 로컬 API로 받아보고 싶다면 로컬 터널로
`http://127.0.0.1:8080`을 외부에 노출한 뒤, GitHub App webhook URL을
`https://<tunnel-host>/v1/github/webhooks`로 설정합니다. 이때는 필요한 환경변수를
명시하고 같은 스크립트를 실행합니다.

```bash
LLM_MODE=litellm \
PUBLISH_MODE=github_app \
UPSTAGE_API_KEY=... \
GITHUB_WEBHOOK_SECRET=... \
GITHUB_APP_ID=... \
GITHUB_APP_PRIVATE_KEY="$(cat path/to/app-private-key.pem)" \
./scripts/local-deploy-test.sh
```

로컬 stack 중지:

```bash
docker compose -f infra/local-deploy/docker-compose.yml -p ai-code-review-agent-local down
```

## GCP Deployment Direction

이 저장소는 GCE VM self-host 배포를 기준으로 구성되어 있습니다. 루트의
`docker-compose.yml`이 `api + postgres(pgvector)`를 한 세트로 실행하므로, 별도의
managed 데이터베이스 없이 VM 한 대에 그대로 배포합니다.

1. GitHub Actions CI에서 ruff와 test를 실행합니다.
2. 수동 CD workflow가 IAP 터널로 VM에 접속해 저장소를 동기화합니다.
3. VM에서 `docker compose up -d --build`로 `api + postgres` stack을 재시작합니다.
4. VM 앞단 리버스 프록시(80/443, TLS)가 내부 `api` 컨테이너(8000)로 트래픽을 전달합니다.

배포 workflow는 `.github/workflows/deploy-gcp-vm.yml`에 있습니다. GCP 배포는 로컬 배포
테스트를 통과한 뒤 수동 실행하도록 구성되어 있습니다. VM의
`~/ai-code-review-agent-deploy/.env`에는 GitHub App, Upstage, DB, Langfuse 값을 먼저
채워둬야 합니다. 자세한 VM/방화벽/고정 IP 구성은 `infra/gcp/README.md`를 참고하세요.

## Project Layout

```text
backend/app/core        domain models, routing, security
backend/app/services    RAG, prompt, LLM, publisher, orchestrator
backend/app/storage     local JSON and PostgreSQL review stores
github-action           PR collector script for GitHub Actions
policies                sample repository review policies
sample-data             local review request payloads
infra/gcp               GCP deployment notes and sample env
infra/local-deploy      Local image-based deployment test compose
scripts                 Local deployment and smoke test helpers
```
