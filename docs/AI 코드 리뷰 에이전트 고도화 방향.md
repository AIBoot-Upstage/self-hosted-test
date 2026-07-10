# AI 코드 리뷰 에이전트 고도화 방향

## 1. 현재 상태 진단

현재 프로젝트는 기초 골격 수준을 넘어, 로컬에서 실제 요청을 넣으면 다음 흐름이 동작하는 상태다.

1. GitHub PR 형태의 payload 입력
2. lint/test 결과와 changed file 분석
3. simple failure, policy context, deep quality 라우팅
4. 로컬 정책 문서 기반 RAG 검색
5. LiteLLM/Solar3 호출을 위한 어댑터 구조
6. 로컬 mock LLM 리뷰 생성
7. FastAPI API 서버
8. Docker Compose 기반 실행
9. GitHub Actions CI/CD와 GCP Cloud Run 배포 골격

다만 높은 평가를 받으려면 "동작하는 백엔드"에서 한 단계 더 나아가야 한다. 평가자가 봤을 때 차별점이 되려면 단순히 LLM으로 리뷰를 생성하는 것이 아니라, 다음 다섯 가지를 증명해야 한다.

1. 왜 이 PR은 low, medium, high 모델로 갔는지 설명 가능해야 한다.
2. RAG가 실제로 저장소 정책을 리뷰에 반영했다는 근거가 보여야 한다.
3. 리뷰 품질, 비용, 속도, 실패 대응을 측정하고 개선할 수 있어야 한다.
4. 프롬프트, 모델, 평가 결과를 버전 관리하는 LLMOps 구조가 있어야 한다.
5. 한 번의 리뷰 요청이 어떤 단계에서 느려지거나 실패했는지 추적 가능한 Observability가 있어야 한다.

## 2. 높은 평가를 위한 핵심 방향

### 방향 1. "라우팅 기반 비용 최적화"를 프로젝트의 주인공으로 만들기

이 프로젝트의 가장 강한 차별점은 AI 코드 리뷰 자체가 아니라, PR 상황에 따라 모델을 다르게 선택하는 라우팅 구조다. 따라서 발표와 구현 모두에서 라우터가 중심에 있어야 한다.

고도화 목표:

1. 라우팅 결과에 `route_name`, `model_tier`, `confidence`, `reasons`, `risk_files`, `changed_lines`를 명확히 남긴다.
2. PR 댓글 상단에 "왜 이 모델을 선택했는지"를 짧게 보여준다.
3. 샘플 PR 9개 이상을 준비해 low/medium/high가 각각 3개 이상 나오게 만든다.
4. 단일 high 모델만 쓰는 방식 대비 예상 비용 절감률을 계산해 보여준다.

평가 어필 포인트:

> 모든 PR에 고성능 모델을 쓰는 단순 구조가 아니라, CI 결과와 코드 위험도를 기반으로 모델 비용을 제어하는 Agentic Workflow를 구현했다.

### 방향 2. RAG를 "있으면 좋은 검색"이 아니라 "저장소 정책 검증 엔진"으로 만들기

RAG는 단순 문서 검색처럼 보이면 평가 임팩트가 약하다. 저장소별 정책, 컨벤션, API 규칙을 리뷰 근거로 사용하는 구조임을 보여줘야 한다.

고도화 목표:

1. `.ai-reviewer/policies/` 아래에 정책 문서를 4개 이상 분리한다.
2. 각 finding에 `policy_source`를 반드시 포함한다.
3. PR 댓글에 "참조한 정책" 섹션을 추가한다.
4. RAG 검색 결과가 없을 때와 있을 때의 리뷰 품질 차이를 데모한다.
5. 가능하면 `LocalPolicyIndex`를 유지하되 `pgvector` 전환 설계 또는 간단한 PostgreSQL 저장 구조를 추가한다.

추천 정책 문서:

| 파일 | 내용 |
| --- | --- |
| `.ai-reviewer/policies/api-style.md` | API 응답 형식, 에러 포맷, status code 규칙 |
| `.ai-reviewer/policies/test-policy.md` | 기능 변경 시 필요한 테스트 기준 |
| `.ai-reviewer/policies/security-policy.md` | 인증, 권한, secret, logging 규칙 |
| `.ai-reviewer/policies/python-style.md` | 함수 크기, 예외 처리, 네이밍 규칙 |

평가 어필 포인트:

> 일반 LLM 리뷰가 아니라 저장소별 정책 문서를 검색하고, 리뷰 finding마다 정책 근거를 연결하는 구조를 만들었다.

### 방향 3. 리뷰 품질 평가 체계를 추가하기

많은 AI 프로젝트는 "잘 되는 것 같다"에서 멈춘다. 높은 평가를 받으려면 자체 평가 체계가 있어야 한다. 완벽한 자동 평가는 아니어도, 샘플 PR과 루브릭을 통해 품질을 측정하면 프로젝트의 완성도가 확 올라간다.

고도화 목표:

1. `eval/sample-prs/`에 low/medium/high 샘플 payload를 저장한다.
2. 각 샘플마다 기대 route, 기대 finding category, 기대 severity를 정의한다.
3. `python -m backend.app.eval_runner` 형태의 평가 스크립트를 만든다.
4. 평가 결과를 `eval/results/latest.json`과 `eval/results/report.md`로 저장한다.
5. 발표 자료에 라우팅 정확도, 정책 근거 포함률, 평균 latency를 넣는다.

추천 평가 지표:

| 지표 | 설명 | 목표 |
| --- | --- | --- |
| Routing Accuracy | 샘플 PR의 기대 route와 실제 route 일치율 | 80% 이상 |
| Policy Citation Rate | medium/high 리뷰에서 policy_source가 포함된 비율 | 80% 이상 |
| Useful Finding Rate | 사람이 유용하다고 판단한 finding 비율 | 60% 이상 |
| Secret Masking Pass Rate | secret 포함 patch가 마스킹되는 비율 | 100% |
| Average Review Latency | 리뷰 요청부터 결과 생성까지 걸린 시간 | mock 기준 3초 이내 |

평가 어필 포인트:

> 단순 데모가 아니라 샘플 PR 세트를 만들고, 라우팅 정확도와 정책 근거 포함률로 Agent 품질을 측정했다.

### 방향 4. LLMOps를 "모델 호출 관리"가 아니라 "리뷰 품질 운영 체계"로 만들기

LLMOps는 LLM을 호출했다는 사실보다, 어떤 프롬프트와 어떤 모델 조합이 어떤 품질을 냈는지 추적하고 개선할 수 있는 구조를 의미한다. 이 프로젝트에서는 PR 리뷰라는 명확한 업무가 있으므로 LLMOps를 과하게 넓히기보다 `prompt version`, `model tier`, `evaluation result`, `cost`, `latency`를 묶어 관리하는 방향이 적합하다.

고도화 목표:

1. route별 prompt template에 `prompt_version`을 부여한다.
2. `model_call`에 `provider`, `model`, `model_tier`, `prompt_version`, `prompt_tokens`, `completion_tokens`, `latency_ms`, `estimated_cost`를 저장한다.
3. 평가 harness에서 prompt/model 조합별 결과를 비교한다.
4. mock, Solar3 low, Solar3 medium, Solar3 high 결과를 같은 schema로 저장한다.
5. 실패한 LLM 응답은 원문을 그대로 노출하지 않고 error type, retry count, fallback route를 남긴다.

LLMOps 관점의 핵심 산출물:

| 산출물 | 설명 |
| --- | --- |
| Prompt Registry | route별 system/user prompt와 version을 관리 |
| Model Call Log | 모델 호출 비용, latency, token 사용량 기록 |
| Evaluation Report | prompt/model 조합별 라우팅 정확도와 finding 품질 비교 |
| Fallback Policy | LLM 실패, JSON 파싱 실패, rate limit 발생 시 대체 흐름 정의 |
| Cost Summary | low/medium/high 라우팅을 통한 예상 비용 절감률 |

평가 어필 포인트:

> LLM 호출을 단발성 기능으로 쓰는 것이 아니라, 프롬프트 버전과 모델 호출 결과를 기록해 품질과 비용을 개선할 수 있는 LLMOps 구조를 설계했다.

### 방향 5. Observability를 "운영 로그"가 아니라 "Agent 의사결정 추적"으로 만들기

Observability는 단순히 로그를 남기는 것이 아니라, 한 번의 리뷰 요청이 어떤 입력을 받아 어떤 판단을 거쳐 어떤 결과를 냈는지 추적 가능하게 만드는 것이다. 이 프로젝트에서는 특히 라우팅 판단, RAG 검색, LLM 호출, GitHub 게시 단계를 하나의 trace로 묶는 것이 중요하다.

고도화 목표:

1. 모든 리뷰 실행에 `review_run_id`와 `trace_id`를 부여한다.
2. `analyze`, `route`, `retrieve`, `prompt_build`, `llm_call`, `validate`, `publish` 단계별 latency를 기록한다.
3. `/v1/metrics/summary`에서 route별 실행 수, 평균 latency, 실패율, token 사용량을 보여준다.
4. 구조화 로그 JSON에 `review_run_id`, `route_name`, `model_tier`, `repository`, `pr_number`, `status`를 포함한다.
5. Cloud Run 배포 시 Google Cloud Logging에서 review_run 단위로 필터링 가능하게 만든다.

추천 Observability 이벤트:

| 이벤트 | 주요 필드 |
| --- | --- |
| `review.started` | `review_run_id`, `repository`, `pr_number`, `head_sha` |
| `review.routed` | `route_name`, `model_tier`, `confidence`, `reasons` |
| `rag.retrieved` | `top_k`, `policy_sources`, `max_score`, `latency_ms` |
| `llm.completed` | `model`, `prompt_version`, `tokens`, `latency_ms`, `status` |
| `review.published` | `publish_mode`, `comment_count`, `github_comment_id` |
| `review.failed` | `step`, `error_type`, `retryable`, `fallback_used` |

평가 어필 포인트:

> Agent가 어떤 근거로 모델을 선택하고 어떤 정책을 검색했으며 어디서 시간이 걸렸는지 trace와 metrics로 설명할 수 있게 만들었다.

## 3. 남은 5일 고도화 로드맵

### Day 1. GitHub PR 데모 완성

목표는 "로컬 payload"가 아니라 "실제 GitHub PR 흐름"을 보여주는 것이다.

할 일:

1. 데모용 GitHub 저장소를 하나 만든다.
2. 데모 저장소 또는 organization에 GitHub App을 설치한다.
3. GitHub App webhook URL을 로컬 tunnel 또는 Cloud Run API로 설정한다.
4. `pull_request`, `check_suite` 이벤트 delivery를 확인한다.
5. PR 댓글이 자동으로 달리는 화면을 캡처한다.

완료 기준:

1. PR 생성 또는 check 완료 시 GitHub webhook delivery가 성공한다.
2. API 서버가 `/v1/github/webhooks` 요청을 받는다.
3. PR에 AI review summary comment가 달린다.

### Day 2. 리뷰 댓글 품질 고도화

목표는 결과물이 "그럴듯한 JSON"이 아니라 실제 리뷰처럼 보이게 만드는 것이다.

할 일:

1. PR summary comment에 라우팅 근거를 추가한다.
2. finding을 severity 순으로 정렬한다.
3. finding category별로 아이콘 없이 간결한 라벨을 붙인다.
4. `policy_source`가 있는 finding은 참조 정책 섹션을 보여준다.
5. line number가 불확실하면 inline comment 대신 summary comment로 degrade한다.

PR 댓글 예시 구조:

```text
## AI Code Review

Route: policy_context_review
Model tier: solar3-medium
Reason: tests passed, repository policy is available

Summary:
테스트는 통과했지만 API 응답 정책과 테스트 정책을 확인해야 합니다.

Findings:
1. medium / api_contract - app/api/profile.py:42
   ...

Referenced Policies:
- api-style.md#Error Response
- test-policy.md#API Behavior Change
```

완료 기준:

1. 평가자가 댓글만 보고도 라우팅 이유를 이해할 수 있다.
2. RAG 근거가 리뷰 결과에 자연스럽게 드러난다.

### Day 3. 평가 harness 구현

목표는 프로젝트가 실험 가능한 시스템임을 보여주는 것이다. 이 단계부터 LLMOps 관점에서 prompt/model 조합의 품질을 비교할 수 있어야 한다.

할 일:

1. `eval/sample-prs/` 디렉터리 생성
2. low, medium, high 샘플 PR 각각 3개씩 작성
3. `expected_route`, `expected_categories`를 포함한 metadata 작성
4. `eval_runner` 구현
5. `eval/results/report.md` 자동 생성
6. route별 `prompt_version`을 결과에 기록
7. model tier별 token, latency, estimated cost를 집계

완료 기준:

1. `python -m backend.app.eval_runner` 실행 시 평가 리포트가 생성된다.
2. 라우팅 정확도, policy citation rate, 평균 latency가 표시된다.
3. prompt/model 조합별 품질과 비용 비교 표가 생성된다.

### Day 4. Observability와 배포 완성도 올리기

목표는 "배포할 계획"이 아니라 "배포 가능한 구조"임을 보여주는 것이다. 동시에 리뷰 실행 단위로 trace, metrics, structured log를 남겨 운영 가능한 Agent처럼 보이게 만든다.

할 일:

1. Cloud Run 배포 README를 더 구체화한다.
2. Secret Manager 변수 목록을 정리한다.
3. GitHub Actions CD workflow에서 필요한 vars/secrets를 표로 문서화한다.
4. `/healthz` 외에 `/readyz`를 추가해 필수 설정 상태를 보여준다.
5. Docker image build 시간을 줄이기 위해 dependency layer cache 구조를 개선한다.
6. `review_run_id` 기반 structured logging 추가
7. `/v1/metrics/summary` API 추가
8. 단계별 latency를 `ReviewResult` 또는 별도 event log에 저장

완료 기준:

1. README만 보고 Cloud Run 배포 준비 항목을 알 수 있다.
2. CI/CD workflow의 목적과 필요한 권한이 명확하다.
3. 컨테이너 health check가 실제로 통과한다.
4. 특정 review run의 route, RAG, LLM, publish 단계를 로그로 추적할 수 있다.
5. route별 실행 수와 평균 latency를 API로 확인할 수 있다.

### Day 5. 발표/시연 시나리오 완성

목표는 "기능 설명"이 아니라 "문제 해결 흐름"을 보여주는 것이다.

추천 데모 순서:

1. 테스트 실패 PR 생성
2. GitHub App Webhook 수신 및 check 완료 감지
3. Agent가 Solar3 low route 선택
4. 실패 원인 요약 댓글 확인
5. 테스트 통과 PR 생성
6. Agent가 RAG 정책 검색 후 Solar3 medium route 선택
7. 정책 근거가 포함된 리뷰 댓글 확인
8. auth/security 변경 PR 생성
9. Agent가 Solar3 high route 선택
10. 평가 리포트에서 routing accuracy와 policy citation rate 확인
11. metrics summary에서 route별 latency와 token 사용량 확인
12. 로그에서 특정 `review_run_id`의 의사결정 trace 확인

완료 기준:

1. 5분 안에 low/medium/high 차이를 모두 설명할 수 있다.
2. 발표자가 "왜 이 프로젝트가 단순 LLM wrapper가 아닌지" 명확히 말할 수 있다.
3. 발표자가 "이 Agent를 운영하면 무엇을 관측하고 개선할 수 있는지" 설명할 수 있다.

## 4. 기능 우선순위

### 반드시 해야 하는 고도화

| 우선순위 | 기능 | 이유 |
| --- | --- | --- |
| P0 | 실제 GitHub PR 댓글 데모 | 평가자가 가장 직관적으로 완성도를 느끼는 지점 |
| P0 | 라우팅 근거 표시 | 프로젝트 핵심 차별점 |
| P0 | 평가 harness | 높은 평가를 위한 객관적 증거 |
| P0 | structured logging과 metrics summary | Observability 키워드를 실제 기능으로 증명 |
| P0 | prompt version과 model call log | LLMOps 키워드를 실제 운영 체계로 증명 |
| P1 | 정책 문서 4종 분리 | RAG가 실제로 의미 있게 보임 |
| P1 | secret masking 테스트 | 코드 리뷰 도구의 안전성 어필 |
| P1 | Cloud Run 배포 문서 구체화 | 실서비스 확장 가능성 어필 |

### 시간이 남으면 좋은 고도화

| 기능 | 기대 효과 |
| --- | --- |
| inline review comment | 실제 코드 리뷰 도구에 가까워짐 |
| PostgreSQL + pgvector | RAG 구현 깊이 강화 |
| Redis queue | 비동기 처리 구조 강화 |
| GitHub Check Run annotation | CI 도구처럼 보이는 완성도 |
| 간단한 metrics endpoint | 운영 관점 강화 |
| prompt versioning | 리뷰 품질 실험 가능 |
| OpenTelemetry trace | Cloud Run/Cloud Logging 연계 시 운영 완성도 강화 |
| 비용 추정 dashboard | 모델 라우팅의 비용 최적화 효과를 직관적으로 제시 |

### 이번 5일 안에 무리하지 않는 것이 좋은 기능

| 기능 | 이유 |
| --- | --- |
| 자동 코드 수정 PR | 안정성 검증 부담이 큼 |
| 자동 approve/merge | 위험하고 평가 포인트 대비 구현 부담이 큼 |
| 풀 웹 대시보드 | 시간 대비 핵심 차별점이 약함 |
| 조직 단위 권한 관리 | MVP 범위를 벗어남 |
| 멀티 VCS 지원 | GitHub PR 데모 완성보다 우선순위가 낮음 |

## 5. 구현 백로그

### Backend

1. `ReviewResult`에 `route.reasons`를 PR 댓글 상단에 더 명확히 표시
2. `/v1/reviews` 외에 `/v1/reviews` 목록 조회 API 추가
3. `/metrics` 또는 `/v1/metrics/summary` 추가
4. `mask_secrets()` 테스트 케이스 강화
5. LLM 응답 schema validation 추가
6. idempotency key 기반 중복 리뷰 방지
7. 실패 시 `status=failed`와 error message 저장
8. `trace_id`와 단계별 latency 저장 구조 추가

### RAG

1. `.ai-reviewer/policies/` 기본 경로 지원
2. policy chunk metadata에 `policy_type`, `section_title`, `source_path` 강화
3. 검색 결과 중복 제거
4. top-k score를 리뷰 결과에 저장
5. RAG on/off 비교용 평가 샘플 작성

### LLMOps

1. route별 prompt template 파일 분리
2. prompt template마다 `prompt_version` 부여
3. `model_call`에 prompt version, retry count, fallback 여부 저장
4. token 사용량 기반 estimated cost 계산
5. JSON schema validation 실패 시 재시도와 fallback 기록
6. eval report에 prompt/model 조합별 결과 비교표 추가
7. Solar3 low, medium, high 모델명을 환경 변수로 관리하고 결과에 기록
8. prompt 변경 시 이전 결과와 비교할 수 있도록 eval baseline 저장

### Observability

1. 모든 요청에 `review_run_id`와 `trace_id` 생성
2. structured logging 포맷 정의
3. `review.started`, `review.routed`, `rag.retrieved`, `llm.completed`, `review.published`, `review.failed` 이벤트 기록
4. 단계별 latency 측정
5. route별 실행 수, 평균 latency, 실패율, token 사용량 집계
6. `/v1/metrics/summary` API 구현
7. `/readyz`에서 LLM mode, publish mode, policy availability 상태 확인
8. Cloud Run + Cloud Logging 필터 예시 문서화

### GitHub Integration

1. PR summary comment 업데이트 방식 적용
2. 기존 bot comment 중복 생성 방지
3. GitHub Check Run status 작성
4. inline comment 가능한 line mapping 개선
5. GitHub App 설치, 권한, webhook delivery 확인 절차 문서화

### Deployment

1. Docker image build cache 최적화
2. Cloud Run service account 권한 정리
3. Secret Manager 설정 문서화
4. GitHub Actions deploy workflow dry-run 절차 작성
5. 운영 환경 `LLM_MODE=litellm`, `PUBLISH_MODE=github_app` 설정 확인
6. Cloud Logging에서 `review_run_id`로 로그 조회하는 방법 문서화
7. Cloud Run revision별 환경 변수와 모델 설정 변경 이력 관리

### Evaluation

1. `eval/sample-prs/low/*.json`
2. `eval/sample-prs/medium/*.json`
3. `eval/sample-prs/high/*.json`
4. `eval/expected/*.json`
5. `backend/app/eval_runner.py`
6. `eval/results/report.md`
7. `eval/results/baseline.json`
8. prompt/model별 비교 결과 표

## 6. 평가용 핵심 메시지

발표나 문서에서 다음 문장을 중심으로 설명하면 좋다.

> 이 프로젝트는 Pull Request를 무조건 하나의 LLM에 보내는 코드 리뷰 도구가 아니라, CI 결과와 변경 위험도, 저장소 정책 검색 결과를 기반으로 리뷰 전략과 모델 티어를 선택하는 AI 코드 리뷰 에이전트입니다.

이어지는 설명:

1. 테스트 실패나 문법 오류는 Solar3 low로 빠르게 원인을 요약한다.
2. 테스트가 통과한 일반 PR은 RAG로 저장소 정책을 검색한 뒤 Solar3 medium으로 리뷰한다.
3. 보안, 인증, DB, 인프라 등 위험한 변경은 Solar3 high로 더 깊게 검토한다.
4. 각 리뷰는 라우팅 근거, 정책 출처, finding severity를 저장하므로 품질 평가와 개선이 가능하다.
5. prompt version, model call log, token/cost/latency를 기록해 LLMOps 관점에서 운영할 수 있다.
6. review run 단위 trace와 structured log를 남겨 Observability 관점에서 장애 원인과 병목 구간을 추적할 수 있다.

## 7. 최종 데모 체크리스트

| 항목 | 완료 기준 |
| --- | --- |
| Docker 실행 | `docker compose up --build -d` 후 `/healthz` 응답 |
| low route | test failure payload가 `solar3-low`로 라우팅 |
| medium route | test passed + policy payload가 `solar3-medium`으로 라우팅 |
| high route | auth/security 변경 payload가 `solar3-high`로 라우팅 |
| RAG 근거 | 리뷰 finding에 `policy_source` 포함 |
| GitHub PR 댓글 | 실제 PR에 AI review comment 게시 |
| 평가 리포트 | routing accuracy와 policy citation rate 표시 |
| LLMOps 로그 | prompt version, model tier, token, cost, latency 기록 |
| Observability | review_run_id 기반 structured log와 metrics summary 확인 |
| 배포 문서 | Cloud Run vars/secrets/권한 정리 |

## 8. 현실적인 5일 목표

5일 안에 가장 높은 효율을 내려면 다음 상태를 목표로 삼는 것이 좋다.

1. Cloud Run 배포 또는 최소 Docker 기반 공개 데모 가능 상태
2. 실제 GitHub PR에서 자동 리뷰 댓글 1회 이상 성공
3. low/medium/high 라우팅 데모 payload 완비
4. RAG 정책 문서 4종과 policy citation 결과
5. 평가 harness와 `report.md`
6. LLMOps 관점의 prompt/model/cost/latency 기록
7. Observability 관점의 structured log와 metrics summary
8. 발표용 5분 데모 시나리오

이 정도까지 완성하면 "기초 골격"이 아니라, 라우팅, RAG, CI/CD, 평가, LLMOps, Observability를 갖춘 Agentic Workflow 프로젝트로 보일 수 있다.
