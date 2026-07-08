# MacBook Staging Deployment

이 문서는 Tailscale로 연결된 MacBook을 staging 서버로 사용하고, GitHub Actions에서 CI를 통과한 뒤 MacBook에 Docker Compose로 배포하는 절차를 정리한다.

배포 흐름:

```text
GitHub push/main or manual dispatch
  -> CI: ruff, pytest, local smoke review
  -> GitHub runner joins tailnet with tailscale/github-action
  -> SSH/SCP to MacBook over Tailscale
  -> docker compose up --build -d
  -> /healthz and /v1/reviews smoke test
```

## 1. MacBook 준비

MacBook에서 먼저 확인한다.

```bash
tailscale status
docker --version
docker compose version
ssh localhost
```

필수 준비:

1. Tailscale 로그인 상태 유지
2. Docker Desktop 실행
3. macOS Remote Login 활성화
4. GitHub Actions가 접속할 SSH public key 등록
5. staging 앱이 위치할 디렉터리 결정

Remote Login 활성화:

```bash
sudo systemsetup -setremotelogin on
```

SSH key 등록 예시:

```bash
ssh-keygen -t ed25519 -C "github-actions-ai-reviewer-staging" -f ./macbook_staging_ed25519 -N ""
mkdir -p ~/.ssh
chmod 700 ~/.ssh
cat ./macbook_staging_ed25519.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

GitHub Secret에는 private key를 넣어야 한다. 줄바꿈 문제를 피하려면 base64로 넣는 방식을 권장한다.

macOS:

```bash
base64 -i ./macbook_staging_ed25519 | pbcopy
```

Linux:

```bash
base64 -w0 ./macbook_staging_ed25519
```

복사한 값을 GitHub secret `MACBOOK_SSH_KEY`에 넣는다. 이름은 `MACBOOK_SSH_KEY`이지만 값은 private key 원문이 아니라 base64 인코딩된 private key다.

권장 앱 경로:

```text
/Users/Shared/ai-code-review-agent
```

주의:

1. workflow는 `MACBOOK_APP_DIR` 안의 기존 파일을 배포 때 교체한다.
2. 단, `.env`와 `.local-data`는 보존한다.
3. 실제 secret 값은 GitHub Actions에서 `.env`로 다시 생성해 MacBook에 업로드한다.

## 2. Tailscale 준비

GitHub-hosted runner가 MacBook의 Tailscale 주소로 접근하려면 workflow 안에서 runner도 tailnet에 들어와야 한다. 이 프로젝트는 `tailscale/github-action@v4`를 사용한다.

Tailscale admin console에서 준비할 것:

1. reusable auth key 생성
2. 가능하면 ephemeral, pre-approved 옵션 사용
3. key 만료 기간을 프로젝트 기간에 맞게 짧게 설정
4. GitHub Actions에서 생성되는 임시 node가 MacBook의 SSH 포트와 API 포트에 접근 가능하도록 ACL 확인

사용할 secret 이름:

```text
TS_AUTHKEY
```

MacBook 주소는 둘 중 하나를 쓴다.

```text
100.x.y.z
macbook-name.tailnet-name.ts.net
```

처음에는 Tailscale IP가 더 단순하다.

## 3. GitHub Repository Secrets

GitHub repository settings에서 `Secrets`를 추가한다.

| 이름 | 예시 | 설명 |
| --- | --- | --- |
| `MACBOOK_HOST` | `100.x.y.z` | MacBook Tailscale IP 또는 MagicDNS 이름 |
| `MACBOOK_USER` | `hojin` | MacBook SSH 사용자명 |
| `TS_AUTHKEY` | `tskey-auth-...` | GitHub runner를 tailnet에 붙일 Tailscale auth key |
| `MACBOOK_SSH_KEY` | 필수 | MacBook에 접속할 private SSH key의 base64 값 |
| `AI_REVIEWER_TOKEN` | 필수 | staging API Bearer token |
| `UPSTAGE_API_KEY` | litellm 모드 필수 | Upstage Solar3 API key |
| `STAGING_GITHUB_TOKEN` | github publish 모드 필수 | PR comment 작성용 GitHub token |

## 4. GitHub Repository Variables

GitHub repository settings에서 `Variables`를 추가한다.

| 이름 | 예시 | 설명 |
| --- | --- | --- |
| `MACBOOK_APP_DIR` | `/Users/Shared/ai-code-review-agent` | MacBook 배포 디렉터리 |
| `STAGING_PORT` | `8080` | API 서버 포트 |

처음 검증은 다음 조합을 권장한다.

```text
llm_mode=mock
publish_mode=local
```

이 조합은 `UPSTAGE_API_KEY`와 `STAGING_GITHUB_TOKEN` 없이도 배포 smoke test가 가능하다.

## 5. 첫 배포 순서

1. MacBook에서 Docker Desktop과 Tailscale 실행 확인
2. GitHub Secrets와 Variables 등록
3. GitHub Actions에서 `Deploy to MacBook Staging` workflow 수동 실행
4. 입력값은 `llm_mode=mock`, `publish_mode=local`로 시작
5. workflow의 test job 통과 확인
6. deploy job에서 Tailscale ping, SSH upload, Docker Compose 실행 확인
7. workflow 마지막 smoke test에서 `/v1/reviews` 성공 확인

MacBook에서 직접 확인:

```bash
cd /Users/Shared/ai-code-review-agent
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
tail -n 40 .local-data/reviews.json
ls -la .local-data/comments
```

다른 tailnet 기기에서 확인:

```bash
curl -fsS http://<macbook-tailscale-ip>:8080/healthz
```

## 6. 실제 PR 리뷰 테스트

staging API를 실제 PR 리뷰에 사용하려면 PR workflow도 Tailscale에 연결되어야 한다. GitHub-hosted runner는 기본적으로 tailnet 내부 주소에 접근할 수 없기 때문이다.

PR 리뷰 workflow에 필요한 흐름:

1. `tailscale/github-action@v4`로 tailnet 접속
2. lint/test 실행
3. `github-action/request_ai_review.py` 실행
4. `AI_REVIEWER_API_URL=http://<macbook-host>:8080`로 staging API 호출

PR에 실제 comment를 달려면 MacBook staging `.env`의 값이 다음처럼 설정되어야 한다.

```text
PUBLISH_MODE=github
GITHUB_TOKEN=<token-with-pr-comment-permission>
```

이 프로젝트의 staging deploy workflow에서는 `publish_mode=github`로 실행하면 `STAGING_GITHUB_TOKEN` secret을 MacBook `.env`의 `GITHUB_TOKEN`으로 넣는다.

## 7. 운영 모드 전환

처음:

```text
llm_mode=mock
publish_mode=local
```

Solar3 호출 확인:

```text
llm_mode=litellm
publish_mode=local
```

실제 GitHub comment 확인:

```text
llm_mode=litellm
publish_mode=github
```

이 순서로 진행하면 문제 발생 위치를 분리하기 쉽다.

## 8. 자주 막히는 지점

### Tailscale ping 실패

확인할 것:

1. MacBook Tailscale이 로그인 상태인지
2. `MACBOOK_HOST` 값이 맞는지
3. `TS_AUTHKEY`가 만료되지 않았는지
4. Tailscale ACL에서 GitHub runner의 임시 node가 MacBook에 접근 가능한지
5. MacBook이 sleep 상태가 아닌지

### SSH 실패

확인할 것:

1. macOS Remote Login이 켜져 있는지
2. `MACBOOK_USER`가 실제 사용자명인지
3. `MACBOOK_SSH_KEY`가 private key를 base64로 인코딩한 값인지
4. private key에 대응하는 public key가 MacBook `authorized_keys`에 들어갔는지
5. private key에 passphrase가 없는지
6. Tailscale ACL에서 port 22 접근이 허용되는지

`Load key "...": error in libcrypto`가 나오면 대부분 private key secret이 줄바꿈 없이 깨졌거나, public key를 잘못 넣었거나, passphrase가 걸린 key를 넣은 경우다. `MACBOOK_SSH_KEY`에는 반드시 `macbook_staging_ed25519` private key 파일을 base64 인코딩한 값을 넣어야 한다. `macbook_staging_ed25519.pub` 파일을 넣으면 안 된다.

### Docker 명령 실패

확인할 것:

1. Docker Desktop이 실행 중인지
2. SSH non-interactive 환경에서 `docker`가 PATH에 있는지
3. workflow는 `/opt/homebrew/bin:/usr/local/bin`을 PATH에 추가한다.

### API는 뜨지만 smoke review 실패

확인할 것:

1. `AI_REVIEWER_TOKEN` secret과 요청 Authorization 값이 같은지
2. `docker compose logs api` 출력
3. `.env`가 MacBook 앱 디렉터리에 생성됐는지

### GitHub comment가 안 달림

확인할 것:

1. `publish_mode=github`로 배포했는지
2. `STAGING_GITHUB_TOKEN`이 설정되어 있는지
3. token에 PR comment 권한이 있는지
4. private repository라면 token scope가 충분한지

## 9. 실제 서버 배포 전 체크리스트

MacBook staging에서 다음을 확인한 뒤 GCP 또는 실제 서버로 넘어간다.

1. Docker staging 배포 성공
2. `/healthz` 성공
3. sample review payload 성공
4. failure payload가 `solar3-low`로 라우팅
5. policy payload가 `solar3-medium`으로 라우팅
6. high-risk payload가 `solar3-high`로 라우팅
7. `llm_mode=litellm` 실제 Solar3 호출 성공
8. `publish_mode=github` 실제 PR comment 성공
9. 로그와 `.local-data/reviews.json`에서 실행 이력 확인
