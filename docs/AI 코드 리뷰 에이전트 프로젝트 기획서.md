# **[미정] 프로젝트 기획서 - AI 코드 리뷰 에이전트**

참여자 : 미정

## **1. 문제 정의 및 프로젝트 개요**

### 프로젝트 한 줄 정의

GitHub Pull Request의 변경사항, 테스트 결과, 저장소 정책을 분석하여 상황에 맞는 AI 모델로 자동 코드 리뷰를 수행하는 라우팅 기반 AI 코드 리뷰 에이전트.

### 서비스 한 줄 정의

개발자가 PR을 올리면 AI가 실패 원인, 코드 품질, 팀 규칙 위반 가능성을 자동으로 검토하고 GitHub 댓글로 리뷰 결과를 제공하는 서비스.

### 서비스 선정 배경

코드 리뷰는 품질을 높이는 핵심 활동이지만, 소규모 팀이나 프로젝트 수업 환경에서는 리뷰 시간이 부족하거나 리뷰 기준이 사람마다 달라지기 쉽다. 또한 모든 변경사항에 고성능 LLM을 사용하는 방식은 비용이 크고, 단순 문법 오류나 테스트 실패처럼 저비용 모델로도 충분한 경우가 많다.

이 프로젝트는 GitHub App Webhook, GitHub API, LangGraph, LiteLLM, Upstage Solar3 API, RAG를 결합하여 리뷰 난이도에 따라 모델을 다르게 선택하는 AI 코드 리뷰 에이전트를 구현한다. 이를 통해 리뷰 품질과 비용 효율을 동시에 개선하는 것을 목표로 한다.

### 해결하려는 문제

1. PR 리뷰가 늦어져 개발 흐름이 지연되는 문제
2. 리뷰어마다 코드 스타일, 테스트 기준, 네이밍 규칙 적용이 달라지는 문제
3. 단순 오류와 복잡한 설계 검토에 같은 수준의 모델을 사용하는 비용 비효율 문제
4. 저장소별 정책, 컨벤션, 문서가 리뷰에 충분히 반영되지 않는 문제
5. 테스트 실패 로그와 코드 변경 내용을 함께 해석하는 데 시간이 오래 걸리는 문제

### 대상 사용자

1. 팀 프로젝트를 진행하는 대학생 개발팀
2. GitHub 기반 협업을 하는 소규모 개발팀
3. PR 리뷰 시간을 줄이고 싶은 프로젝트 리더 또는 코드 리뷰어
4. 저장소별 코드 규칙을 일관되게 적용하고 싶은 maintainer

### 핵심 가치

이 프로젝트의 핵심 가치는 "상황에 맞는 모델 라우팅을 통해 비용은 줄이고, 저장소 맥락을 반영한 리뷰 품질은 높이는 것"이다. 단순 오류는 빠르고 저렴하게 처리하고, 정책 기반 검토나 복잡한 설계 검토는 더 많은 컨텍스트와 고품질 모델을 사용하여 리뷰 결과의 실용성을 높인다.

## **2. 사용자 및 Agent 설계**

### 타깃 사용자 페르소나

| 구분 | 설명 |
| --- | --- |
| 이름 | 김개발 |
| 연령대 | 20대 초중반 |
| 직업/상황 | 팀 프로젝트를 진행하는 대학생 개발자 |
| 특징 | GitHub PR 기반 협업은 하고 있지만 코드 리뷰 경험이 많지 않음 |
| 불편함 | 리뷰 기준이 명확하지 않고, 테스트 실패 원인을 빠르게 파악하기 어려움 |
| 기대 | PR을 올리면 자동으로 문제 지점과 개선 방향을 알려주는 도구를 원함 |

### Agent의 역할

AI 코드 리뷰 에이전트는 GitHub PR 이벤트를 기준으로 다음 역할을 수행한다.

1. PR 변경 파일과 diff를 수집한다.
2. GitHub Checks API에서 문법 검사, lint, test 결과를 수집한다.
3. 변경사항의 위험도와 테스트 상태를 기준으로 리뷰 라우팅을 수행한다.
4. 저장소 정책, 코드 컨벤션, 문서 규칙을 RAG로 검색하여 리뷰에 반영한다.
5. LiteLLM을 통해 Upstage Solar3 모델군을 호출한다.
6. 리뷰 결과를 파일별 댓글, PR 요약 댓글, Check Run 결과로 GitHub에 게시한다.
7. 리뷰 실행 결과, 사용 모델, 토큰 사용량, 발견된 이슈를 저장한다.

### Agent의 성격 및 톤앤매너

1. 근거 중심: 추측보다 diff, 테스트 로그, 저장소 정책을 근거로 말한다.
2. 실용 중심: 단순 비판보다 수정 방향과 예시를 제공한다.
3. 간결함: PR 리뷰 댓글은 짧고 명확하게 작성한다.
4. 우선순위 중심: 치명적인 문제, 테스트 실패, 정책 위반 가능성을 먼저 제시한다.
5. 협업 친화적: 명령형보다 제안형 표현을 사용한다.

### Agent의 자율성 범위

| 항목 | 허용 여부 | 설명 |
| --- | --- | --- |
| PR diff 읽기 | 허용 | GitHub API로 변경 파일과 diff를 읽는다. |
| 테스트/빌드 로그 분석 | 허용 | 실패 로그를 요약하고 관련 코드 위치를 추정한다. |
| 모델 라우팅 결정 | 허용 | 사전 정의된 규칙과 위험도 점수에 따라 low/medium/high 경로를 선택한다. |
| RAG 검색 | 허용 | 저장소 문서와 정책에서 관련 규칙을 검색한다. |
| GitHub 댓글 작성 | 허용 | 리뷰 결과를 PR comment 또는 review comment로 작성한다. |
| 코드 자동 수정 commit | 제외 | MVP에서는 직접 코드를 수정하거나 push하지 않는다. |
| PR approve/merge | 제외 | 최종 승인과 병합은 사람에게 맡긴다. |
| 민감정보 저장 | 제한 | 토큰, 비밀키, 원문 로그 저장은 최소화하고 필요 시 마스킹한다. |

## **3. 핵심 기능 및 사용자 흐름**

### 주요 사용자 시나리오

#### 시나리오 1: 테스트가 실패한 PR

개발자가 PR을 생성하면 GitHub App Webhook이 PR 이벤트를 수신하고, CI check 완료 이후 GitHub Checks API로 lint/test 상태를 확인한다. 테스트가 실패하면 에이전트는 실패 상태와 관련 diff를 분석하고 Solar3 low 경로로 라우팅한다. 결과 댓글에는 실패한 테스트, 예상 원인, 수정 후보 파일, 우선 확인할 코드를 간단히 제시한다.

#### 시나리오 2: 테스트는 통과했지만 저장소 규칙 검토가 필요한 PR

PR의 기본 문법 검사와 테스트가 통과하면 에이전트는 저장소의 `CONTRIBUTING.md`, `README.md`, `docs/`, `.ai-reviewer/policies/` 문서를 RAG로 검색한다. Solar3 medium 경로에서 변경 코드와 관련 정책을 함께 검토하여 네이밍, 예외 처리, 테스트 작성 방식, API 응답 형식 등의 규칙 위반 가능성을 리뷰한다.

#### 시나리오 3: 변경 범위가 크거나 위험도가 높은 PR

인증, 결제, 권한, 데이터베이스 마이그레이션, 보안 관련 코드처럼 위험도가 높은 변경이 감지되거나 diff 크기가 큰 경우 에이전트는 Solar3 high 경로로 라우팅한다. 이 경로에서는 단순 규칙 검토를 넘어 설계 관점, 잠재적 장애 가능성, 보안 리스크, 유지보수성까지 검토한다.

### 핵심 기능 정의

| 기능 | 설명 | MVP 포함 |
| --- | --- | --- |
| GitHub App Webhook 연동 | PR/check 이벤트 발생 시 리뷰 실행 | 포함 |
| GitHub API 연동 | PR diff, 파일 목록, 댓글 작성, Check Run 갱신 | 포함 |
| 테스트/문법 결과 수집 | lint, test, build 결과를 리뷰 입력으로 사용 | 포함 |
| 라우팅 엔진 | 실패 상태, 위험도, 정책 필요도에 따라 모델 티어 선택 | 포함 |
| LiteLLM 모델 어댑터 | Solar3 API 호출을 추상화하고 모델 교체 가능하게 구성 | 포함 |
| RAG 정책 검색 | 저장소 정책과 컨벤션을 검색해 리뷰 프롬프트에 주입 | 포함 |
| 리뷰 댓글 생성 | 파일별 리뷰와 PR 요약 댓글 생성 | 포함 |
| 실행 이력 저장 | route, 모델, 토큰, 리뷰 결과, latency 저장 | 포함 |
| 웹 대시보드 | 리뷰 실행 기록과 지표 시각화 | MVP 이후 |
| 자동 수정 PR 생성 | AI가 직접 수정 branch 생성 | MVP 이후 |

### 사용자 관점 워크플로우

1. maintainer가 repository 또는 organization에 AI 리뷰 GitHub App을 설치한다.
2. GitHub App webhook URL과 webhook secret을 설정한다.
3. 선택적으로 `.ai-reviewer.yml`과 `.ai-reviewer/policies/*.md`에 리뷰 정책을 작성한다.
4. 개발자가 GitHub에 PR을 생성하거나 업데이트한다.
5. GitHub App Webhook이 PR 이벤트와 check 완료 이벤트를 수신한다.
6. AI 코드 리뷰 에이전트가 GitHub API로 diff, 변경 파일, check 결과를 수집한다.
7. 에이전트가 PR 상태를 분석하고 적절한 모델 경로를 선택한다.
8. 에이전트가 GitHub PR에 요약 댓글과 파일별 리뷰 댓글을 남긴다.
9. 개발자는 댓글을 참고하여 코드를 수정하고 다시 push한다.

### 시스템 관점 워크플로우

1. GitHub App Webhook이 `pull_request` 이벤트를 수신한다.
2. 기본 모드에서는 PR 이벤트를 저장하고 `check_suite.completed` 이벤트를 기다린다.
3. GitHub App installation token을 발급한다.
4. GitHub API로 PR diff, 변경 파일, 커밋 SHA, 작성자 정보, check 결과를 수집한다.
5. Backend 내부 리뷰 실행 파이프라인이 Review Run을 생성한다.
6. Analyzer가 테스트 실패 여부, 변경 파일 유형, 위험도, diff 크기를 계산한다.
7. Router가 Solar3 low, medium, high 중 하나를 선택한다.
8. medium/high 경로에서는 RAG Retriever가 저장소 정책과 관련 문서를 검색한다.
9. Prompt Builder가 route별 프롬프트와 출력 스키마를 구성한다.
10. LiteLLM Gateway가 Upstage Solar3 API를 호출한다.
11. Response Validator가 JSON 형식, 라인 범위, 중복 댓글을 검증한다.
12. GitHub Publisher가 PR comment, review comment, check result를 작성한다.
13. Persistence Layer가 리뷰 이력, finding, 모델 호출 로그를 저장한다.

## **4. 기술 구현 설계**

### 기술 스택

| 영역 | 기술 |
| --- | --- |
| 언어 | Python 3.11+ |
| Backend API | FastAPI |
| Agent orchestration | LangGraph StateGraph, queue worker |
| LLM Gateway | LiteLLM |
| LLM Provider | Upstage Solar3 API |
| GitHub 연동 | GitHub App Webhook, GitHub REST API, GitHub Checks API |
| RAG | PostgreSQL + pgvector 또는 별도 Vector DB |
| DB | PostgreSQL |
| Queue/Cache | Redis, RQ 또는 Celery |
| 테스트 | pytest, ruff, mypy |
| 배포 | Docker, Docker Compose |
| 관측성 | structured logging, review_run metrics, model_call metrics |

### 시스템 아키텍처

상세 아키텍처는 `AI 코드 리뷰 에이전트 기술 설계서.md`에 별도 정리한다. 핵심 구조는 GitHub App Webhook이 PR/check 이벤트를 수신하고, Backend API가 GitHub API 수집, 라우팅/RAG/LLM 호출/GitHub 댓글 작성을 담당하는 방식이다.

### 모델 라우팅 전략

| 조건 | 라우트 | 모델 티어 | 리뷰 초점 |
| --- | --- | --- | --- |
| 기본 문법 오류, lint 실패, test 실패 | simple_failure_review | Solar3 low | 실패 원인 요약, 수정 우선순위, 관련 파일 제안 |
| 문법/테스트 통과, 저장소 정책 기반 검토 필요 | policy_context_review | Solar3 medium | 컨벤션, 표기 규칙, 예외 처리, 테스트 관례 |
| 대규모 변경, 보안/권한/DB/인프라 변경, 낮은 라우팅 확신도 | deep_quality_review | Solar3 high | 설계 리스크, 보안, 성능, 유지보수성, 다른 관점의 리뷰 |

### 프롬프트 설계 전략

1. 입력은 PR metadata, diff summary, changed files, test result, retrieved policies로 구조화한다.
2. 출력은 JSON schema를 먼저 생성한 뒤 GitHub 댓글 형식으로 변환한다.
3. 모든 finding은 `severity`, `category`, `file_path`, `line`, `message`, `suggestion`, `evidence`를 포함한다.
4. RAG를 사용한 경우 어떤 정책 문서에서 가져온 근거인지 `policy_source`를 함께 남긴다.
5. 확실하지 않은 내용은 단정하지 않고 "확인 필요"로 표시한다.
6. route별로 리뷰 범위를 제한하여 low 경로에서 과도한 설계 리뷰가 나오지 않게 한다.

### 데이터 활용 및 기억 관리

| 데이터 | 활용 방식 | 저장 여부 |
| --- | --- | --- |
| PR diff | 리뷰 입력, 파일별 comment 위치 계산 | Review Run 단위로 요약 저장 |
| 테스트 로그 | 실패 원인 분석 | 원문은 최소 저장, 요약/해시 중심 저장 |
| 저장소 정책 문서 | RAG index 생성 | chunk와 embedding 저장 |
| 리뷰 결과 | 재리뷰 중복 방지, 품질 평가 | 저장 |
| 모델 호출 로그 | 비용/성능 분석 | 토큰, latency, route 저장 |
| GitHub token | API 호출 인증 | GitHub App private key로 installation token을 단기 발급 |

### 제약사항 및 예외 처리

1. GitHub API rate limit에 도달하면 리뷰를 실패 처리하지 않고 재시도 가능한 상태로 저장한다.
2. diff가 너무 크면 파일 우선순위를 계산하여 핵심 파일만 리뷰한다.
3. 테스트 로그가 너무 길면 실패 traceback, assertion, 에러 메시지 중심으로 요약한다.
4. LLM 응답이 JSON schema를 위반하면 1회 재시도하고, 실패 시 PR 요약 댓글만 남긴다.
5. RAG index가 없거나 비어 있으면 medium 경로에서 일반 코드 품질 리뷰로 degrade한다.
6. 라인 번호 매칭이 실패하면 파일별 inline comment 대신 PR summary comment로 대체한다.
7. 비밀키, 토큰, 개인정보로 보이는 문자열은 LLM 호출 전에 마스킹한다.
8. MVP에서는 자동 수정, 자동 승인, 자동 병합 기능을 제공하지 않는다.

## **5. 성과 평가 및 실행 계획**

### 성공 지표(KPI)

| 지표 | 목표 |
| --- | --- |
| PR 리뷰 자동 생성 성공률 | 90% 이상 |
| 테스트 실패 PR의 원인 요약 정확도 | 팀원 평가 기준 4점/5점 이상 |
| RAG 기반 정책 리뷰의 근거 포함률 | 80% 이상 |
| 평균 리뷰 생성 시간 | 5분 이내 |
| low/medium/high 라우팅 정확도 | 샘플 PR 기준 80% 이상 |
| 불필요한 고성능 모델 호출 감소율 | 단일 high 모델 사용 대비 30% 이상 |
| 사람이 유용하다고 판단한 comment 비율 | 60% 이상 |

### MVP 범위

#### 반드시 구현

1. GitHub App Webhook 기반 PR/check 이벤트 트리거
2. PR diff, changed files, test/lint result 수집
3. Backend API의 리뷰 요청 수신
4. 라우팅 엔진 구현
5. LiteLLM을 통한 Solar3 low/medium/high 호출
6. 저장소 정책 문서 RAG indexing 및 retrieval
7. PR summary comment 작성
8. 파일별 review comment 작성
9. review run, finding, model call 저장
10. 로컬 Docker Compose 실행 환경

#### 이번 범위에서 제외

1. AI 자동 코드 수정 commit
2. PR 자동 approve/merge
3. 실시간 웹 대시보드
4. 다중 VCS 지원
5. 조직 단위 권한 관리
6. 상용 수준의 과금/사용량 제한 시스템

### 단계별 개발 로드맵

| 단계 | 목표 | 산출물 |
| --- | --- | --- |
| 1단계 | 요구사항 및 저장소 설정 | `.ai-reviewer.yml`, GitHub App/Webhook 설정, API schema |
| 2단계 | PR 데이터 수집 | diff/test/lint 결과 JSON 생성, GitHub API client |
| 3단계 | low 라우트 구현 | 실패 로그 기반 simple review, PR summary comment |
| 4단계 | RAG 구현 | 정책 문서 indexing, pgvector 검색, medium route prompt |
| 5단계 | high 라우트 구현 | 위험도 계산, deep review prompt, 비용/latency 기록 |
| 6단계 | 댓글 품질 개선 | schema validation, 중복 제거, line mapping |
| 7단계 | 평가 및 데모 | 샘플 PR 세트, KPI 측정, 발표용 시나리오 |

### 기대 효과

1. PR 리뷰 대기 시간을 줄이고 개발자가 더 빠르게 피드백을 받을 수 있다.
2. 저장소별 규칙과 컨벤션을 일관되게 적용할 수 있다.
3. 단순 오류와 복잡한 리뷰를 구분하여 LLM 사용 비용을 줄일 수 있다.
4. 리뷰어는 반복적인 오류 확인보다 설계와 의사결정에 집중할 수 있다.
5. 프로젝트 수업 환경에서도 일정 수준 이상의 코드 리뷰 경험을 제공할 수 있다.
