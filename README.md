# AI Code Review Agent

GitHub Pull Request의 diff, lint/test 결과, 저장소 정책을 분석해 상황별로
동일한 Solar3 모델의 low/medium/high 추론 강도를 선택하는 AI 코드 리뷰 에이전트입니다.

프로젝트 전체를 코드 없이 파악하려면 [프로젝트 현황](docs/프로젝트%20현황.md)을 먼저
읽으세요. 다른 사람에게 설명하기 위한 문제, 가치와 핵심 개념은
[프로젝트 설명서](docs/프로젝트%20설명서.md)에 정리되어 있습니다. 코드를 직접 읽기
시작할 때는 [코드 읽기 가이드](docs/코드%20읽기%20가이드.md)가 추천 순서와 방법을
안내합니다(모든 `.py` 파일에는 한국어 주석이 달려 있습니다).

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

`.env.example`은 로컬 build 테스트를 위해
`COMPOSE_FILE=docker-compose.yml:docker-compose.local.yml`로 되어 있습니다. GCP VM처럼
이미 배포된 image만 실행하려면 `COMPOSE_FILE=docker-compose.yml`로 바꿉니다.
VM에서 Caddy reverse proxy/TLS까지 같이 실행하려면 `.env`에
`COMPOSE_PROFILES=edge`와 `DOMAIN=<domain>`을 추가합니다.

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
SOLAR3_LOW_MAX_TOKENS=4096
SOLAR3_MEDIUM_MAX_TOKENS=8192
SOLAR3_HIGH_MAX_TOKENS=16384
```

`max_tokens`는 내부 추론에서도 소모될 수 있어 표준 리뷰는 8,192, 선택형 심층 리뷰는
16,384로 둡니다. 입력과 생성 상한의 합은 모델 context 이하여야 하며, 실제 사용량은
Langfuse와 `review_runs.model_call`로 확인합니다.

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
| user requests the GitHub Check action | `deep_quality_review` | `solar3-high` / `high` |

## LangGraph Workflow

리뷰 실행 파이프라인은 LangGraph `StateGraph`로 구성됩니다.

```text
create_review
 -> extract_features
 -> select_route
 -> select_harness
 -> retrieve_policies 또는 skip_policy_retrieval
 -> build_prompt
 -> call_llm
 -> validate_findings
 -> assemble_result
 -> persist_result
 -> publish_comment
 -> complete_review
```

각 node는 SSE 이벤트를 발행하므로 리뷰 실행 상태를 단계별로 추적할 수 있습니다.
로컬 가상환경에 LangGraph가 설치되어 있지 않은 경우 테스트 편의를 위해 같은 node/edge
순서를 따르는 fallback executor가 동작하지만, 배포 환경은 `pyproject.toml`의
`langgraph` 의존성으로 실제 LangGraph 런타임을 사용합니다.

모델 결과는 바로 게시하지 않습니다. `validate_findings`에서 changed file, right-side diff line,
정책 출처, 선택된 knowledge card ID와 severity 상한, 중복과 route별 최대 개수를 검증합니다.
허용 카드 ID가 없거나 임의 ID를 사용한 finding은 폐기합니다. 검증된 line finding은 GitHub inline
review로, 나머지는 PR-level summary comment로 게시합니다.

## Review Evaluation Data

공개 저장소의 PR review와 inline comment를 평가용 JSONL로 수집할 수 있습니다. 결과는 기본적으로
Git에서 제외되는 `.local-data/`에 저장합니다.

```bash
GITHUB_TOKEN=... python -m scripts.collect_open_source_reviews \
  openai/codex --max-prs 25
```

수집 데이터는 maintainer finding 재현율, 승인 상태 false-positive, 정책 미주입/주입 A/B 평가에
사용하며 model input에는 maintainer comment와 이후 patch를 넣지 않습니다.

## Repository Policies

Docker/배포 기본값은 Postgres `policy_chunks`에 정책 chunk를 동기화한 뒤 검색합니다.
`POLICY_ROOT` 아래 Markdown, CODEOWNERS, PR template을 policy chunk로 분리해
`policy_chunks` 테이블에 저장합니다. DB가 없는 로컬 개발에서는 같은 인터페이스로
파일 기반 keyword retrieval fallback을 사용할 수 있습니다.

기본 공통 정책 세트:

```text
policies/api-contract.md
policies/github-review-workflow.md
policies/testing-and-routing.md
policies/security-and-privacy.md
policies/performance-and-maintainability.md
policies/observability-and-reliability.md
```

현재 Postgres backend도 검색 자체는 lexical overlap을 사용하며 `embedding` 컬럼은 vector/hybrid
retrieval 전환을 위한 예약 필드입니다. 설치 대상 repository의 문서를 자동 수집하는 방식이
아니라, 배포된 위 정책 세트를 공통 기준으로 사용합니다.

### Review Harness

리뷰에는 모든 정책을 넣지 않습니다. 서비스 소유 하네스가 diff와 CI에서 신호를 추출하고,
관련 `SKILL.md` 절차와 정책 유형을 고른 뒤 배치마다 최대 2개 정책 chunk만 prompt에 전달합니다.
선택된 skill 안에서는 path와 patch marker가 맞는 knowledge card만 추가해 필요한 증거와 오탐
방지 조건을 전달합니다. 삭제 line은 선택 신호에서 제외하고, 입력 sink와 API breaking처럼 오탐
비용이 큰 카드는 path와 patch 조건을 함께 요구합니다. PR 요약 댓글은 `변경 요약`,
`파일별 변경 요약`, `리뷰 근거`, `리뷰` 네 구역으로 출력됩니다. inline finding도 리뷰 구역에 함께
표시되며, 재실행할 때 자동 리뷰와 심층 리뷰의 기존 댓글을 각각 최신 결과로 갱신합니다.
자동 표준 리뷰에서는 범용 동작 카드를 사용하지 않으며, 구체적인 카드가 없는 batch는 finding을
억지로 만들지 않습니다. route 전체 finding 상한은 batch 수로 나눠 각 호출에 적용합니다.

`리뷰 근거` 구역은 이번 리뷰가 실제로 적용한 검토 절차(skill), 참조한 저장소 정책 문서,
참고한 외부 지식 카드를 한곳에 모아 보여 줍니다. 지식 카드는 `review_harness/references/sources.json`에
등록된 공식 출처의 제목과 링크로 표시되어, "이 지적이 어떤 문서를 근거로 나왔는지"를 클릭 한 번으로
확인할 수 있습니다. 개별 finding의 `검토 기준`에도 카드 ID 대신 카드 제목과 출처 링크가 함께
표시됩니다.

skill과 knowledge card는 하나 이상의 `source_id`를 가져야 합니다. 공식 출처 registry에 없는 ID,
중복 ID, HTTPS가 아닌 출처, 증거·오탐 조건이 비어 있는 카드는 하네스 초기화 단계에서 거부합니다.
외부 지식 카드는 검토 관점일 뿐 저장소 정책이 아니므로 `policy_source`로 인용하지 않습니다.

```text
review_harness/manifest.json
review_harness/scripts/diff_signals.py
review_harness/skills/*/SKILL.md
review_harness/references/knowledge-cards.json
review_harness/references/sources.json
review_harness/evaluation/policy-selection-fixtures.json
```

하네스 선택 baseline은 다음 명령으로 재현합니다.

```bash
python -m review_harness.scripts.evaluate_harness
```

이 스크립트의 Recall은 skill·정책 선택 정확도이며, 최종 LLM finding의 정확도는 별도의
오픈소스 PR snapshot 평가로 측정해야 합니다. 보안을 위해 설치 저장소가 제공하는 임의 script나
skill은 실행하지 않고 서비스 image에 포함된 검증된 하네스만 실행합니다.

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
Checks: Read and write
Metadata: Read
```

권장 구독 이벤트:

```text
pull_request
check_suite
check_run
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
5. `PUBLISH_MODE=github_app`이면 App ID와 private key의 실제 GitHub JWT 인증을 확인합니다.
6. 로컬 게시 모드이면 `/v1/reviews?wait=true`로 동기식 리뷰 smoke test를 실행합니다.
7. `/v1/github/webhooks`에 서명된 `ping` webhook을 보내 signature 검증을 확인합니다.

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
`docker-compose.yml`이 `api + postgres(pgvector)`를 기본 세트로 실행하고, VM에서는
`COMPOSE_PROFILES=edge`로 `caddy` reverse proxy/TLS 서비스를 함께 실행합니다. 별도의
managed 데이터베이스 없이 VM 한 대에 그대로 배포합니다.

1. main push 이후 GitHub Actions CI에서 ruff와 test를 실행합니다.
2. CI가 성공하면 CD workflow가 Docker image를 빌드합니다.
3. WIF로 GCP 인증 후 IAP 터널로 VM에 image tar와 runtime 파일을 업로드합니다.
4. VM에서 `docker load` 후 `docker compose up -d --no-build`로 stack을 재시작합니다.
5. `caddy`가 80/443에서 TLS를 종료하고 내부 `api` 컨테이너의 `PORT`로 트래픽을 전달합니다.
   `Caddyfile`이 참조하는 `DOMAIN`을 VM `.env`에 설정해야 자동 HTTPS가 동작합니다
   (도메인이 없다면 `sslip.io` 등으로 대체 가능, 자세한 내용은 `infra/gcp/README.md` 참고).

배포 workflow는 `.github/workflows/deploy-gcp-vm.yml`에 있습니다. VM의
`/opt/ai-code-review-agent-deploy/.env`에는 GitHub App, Upstage, DB, Langfuse 값을 먼저
채워둬야 합니다. Caddy를 사용하려면 같은 `.env`에 `COMPOSE_PROFILES=edge`와 `DOMAIN`도
설정합니다. GitHub repository variable `CD_DEPLOY_TARGET=local-only`로 바꾸면 main push 후
image build 검증만 하고 GCP 배포는 건너뜁니다. 자세한 VM/방화벽/고정 IP 구성은
`infra/gcp/README.md`를 참고하세요.

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
