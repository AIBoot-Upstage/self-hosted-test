# 발표 화면 캡처 목록

이 폴더에는 생성 이미지보다 실제 동작 화면을 우선 저장한다. 아래 파일명으로 맞추면 슬라이드와
대본에서 자료를 찾기 쉽다.

| 파일명 | 캡처 내용 | 사용 슬라이드 |
| --- | --- | ---: |
| `01-pr-checks.png` | `CI / ci`와 `AI Code Review`가 완료된 PR Checks | 1, 8 |
| `02-failure-review.png` | 실패 원인 우선 한글 리뷰와 실패 check 근거 | 4, 8 |
| `03-policy-review.png` | 정책 출처, 파일, line이 포함된 표준 리뷰 | 3, 6, 8 |
| `04-deep-review-action.png` | GitHub Check의 `심층 리뷰 실행` 버튼 | 4, 8 |
| `05-deep-review-result.png` | 복잡도·간소화 finding이 포함된 심층 결과 | 4, 8 |
| `06-langfuse-trace.png` | 실제 prompt, output, model, token, latency, metadata | 7~9 |
| `07-github-actions-ci.png` | lint/smoke와 test 병렬 실행 및 aggregate CI | 4, 5 |
| `08-gcp-cd-success.png` | WIF/IAP 기반 배포 workflow 성공 | 5, 9 |
| `09-review-runs-db.png` | provider, model, route, token이 저장된 DB 조회 | 9 |

## 캡처 기준

- 브라우저 배율과 창 크기를 통일한다.
- finding 본문과 정책 출처가 한 화면에서 읽히도록 자른다.
- 성공 화면만 모으지 말고 실패 경로와 표준 경로를 각각 포함한다.
- Langfuse 화면은 `review_run_id`와 route가 연결되는 부분을 포함한다.
- 슬라이드에는 작은 원본 여러 개보다 핵심 영역을 확대한 캡처 한 개를 사용한다.

## 보안 점검

다음 값이 캡처에 보이면 반드시 가린다.

- Upstage API key, GitHub token, GitHub App private key
- webhook secret, reviewer token, DB password
- WIF provider 세부 값 중 공개가 불필요한 조직 정보
- 비공개 저장소 이름, 사용자 이메일, 내부 도메인 또는 IP
- Langfuse prompt에 포함된 비공개 소스 코드와 민감 데이터

가림 처리 후에도 모델명, route, reasoning effort, token, latency, 정책 출처처럼 발표 근거가 되는
필드는 남긴다.
