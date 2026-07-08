# MacBook Staging Deployment

이 문서는 Tailscale로 연결된 MacBook을 staging 서버로 사용하고, GitHub Actions에서 CI를 통과한 뒤 MacBook에 Docker Compose로 배포하는 절차를 정리한다.

배포 흐름:

```text
GitHub push/main or manual dispatch
  -> CI: ruff, pytest, local smoke review
  -> build Docker image and push it to GHCR
  -> GitHub runner joins tailnet with tailscale/github-action
  -> SSH/SCP .env, GHCR auth config, and staging compose file to MacBook
  -> docker compose pull && docker compose up -d --no-build: api + postgres(pgvector)
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

GitHub Secret에는 private key를 넣어야 한다. 줄바꿈 문제를 피하려면 base64로 넣는 방식을 권장하지만, workflow는 raw private key 원문도 지원한다.

macOS:

```bash
base64 -i ./macbook_staging_ed25519 | pbcopy
```

Linux:

```bash
base64 -w0 ./macbook_staging_ed25519
```

복사한 값을 GitHub secret `MACBOOK_SSH_KEY`에 넣는다. 권장값은 base64 인코딩된 private key다. raw private key 원문을 넣어도 동작하지만, 줄바꿈이 깨질 수 있으므로 base64 방식이 더 안전하다.

권장 앱 경로:

```text
/Users/Shared/ai-code-review-agent
```

주의:

1. workflow는 앱 소스코드 tarball을 MacBook에 보내지 않는다.
2. GitHub Actions가 GHCR에 image를 push하고, MacBook은 해당 image를 pull한다.
3. workflow는 `MACBOOK_APP_DIR` 안의 기존 소스 파일을 배포 때 정리한다.
4. 단, `.local-data`는 보존한다.
5. 실제 secret 값은 GitHub Actions에서 `.env`로 다시 생성해 MacBook에 업로드한다.

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
| `MACBOOK_SSH_KEY` | 필수 | MacBook에 접속할 private SSH key 또는 그 base64 값 |
| `AI_REVIEWER_TOKEN` | 필수 | staging API Bearer token |
| `UPSTAGE_API_KEY` | litellm 모드 필수 | Upstage Solar3 API key |
| `GHCR_TOKEN` | 조건부 | `GITHUB_TOKEN`으로 GHCR push가 막힐 때 사용하는 PAT |
| `POSTGRES_PASSWORD` | 선택 | staging Postgres password. 없으면 `reviewer` |
| `STAGING_GITHUB_TOKEN` | github publish 모드 필수 | PR comment 작성용 GitHub token |

## 4. GitHub Repository Variables

GitHub repository settings에서 `Variables`를 추가한다.

| 이름 | 예시 | 설명 |
| --- | --- | --- |
| `MACBOOK_APP_DIR` | `/Users/Shared/ai-code-review-agent` | MacBook 배포 디렉터리 |
| `STAGING_PORT` | `8080` | API 서버 포트 |
| `GHCR_USER` | GitHub username | `GHCR_TOKEN`을 발급한 GitHub 사용자명. 없으면 workflow actor |
| `POSTGRES_DB` | `reviewer` | staging Postgres database |
| `POSTGRES_USER` | `reviewer` | staging Postgres user |
| `SOLAR3_MODEL` | `solar3` | LiteLLM에 전달할 실제 Solar3 model id |
| `SOLAR3_LOW_REASONING_EFFORT` | `low` | `solar3-low` review tier의 추론 강도 |
| `SOLAR3_MEDIUM_REASONING_EFFORT` | `medium` | `solar3-medium` review tier의 추론 강도 |
| `SOLAR3_HIGH_REASONING_EFFORT` | `high` | `solar3-high` review tier의 추론 강도 |

## 5. GHCR Image 배포 방식

MacBook staging workflow는 소스코드 압축 파일을 서버에 보내지 않는다. 대신 GitHub Actions에서 다음 image를 빌드하고 GHCR에 push한다.

```text
ghcr.io/<owner>/<repo>:<commit-sha>
ghcr.io/<owner>/<repo>:staging
```

MacBook에는 `.env`, GHCR pull 인증 config, `infra/macbook-staging/docker-compose.yml`만 전송된다. 실제 실행은 `AI_REVIEWER_IMAGE=ghcr.io/<owner>/<repo>:<commit-sha>` 값을 사용해 image를 pull하는 방식이다.

Docker Hub 계정은 필요 없다. GHCR push/pull은 workflow의 `GITHUB_TOKEN`과 job permission으로 처리한다.

필요한 workflow 권한:

```yaml
permissions:
  contents: read
  packages: write
```

private GHCR image도 같은 workflow 안에서 MacBook에 임시 pull config를 전달하므로 별도의 Docker Hub ID/PW는 필요 없다.

조직 정책 때문에 repository settings에서 `Read and write permissions`를 선택할 수 없거나, `GITHUB_TOKEN`의 `packages: write`가 막혀 있으면 PAT를 사용한다.

필요한 값:

```text
Secret: GHCR_TOKEN
Variable: GHCR_USER
```

`GHCR_TOKEN`은 GitHub Packages 인증용 classic PAT를 권장한다. 최소 scope는 다음과 같다.

```text
write:packages
read:packages
```

조직 repository/package에 접근해야 하거나 private package 권한 문제가 나면 `repo` scope가 추가로 필요할 수 있다. 조직에서 SAML SSO를 쓰는 경우 PAT를 해당 조직에 authorize해야 한다.

## 6. 배포 모드

기본 배포 조합은 다음과 같다.

```text
llm_mode=litellm
publish_mode=local
```

이 조합은 실제 Solar3 호출까지 확인한다. API key 없이 네트워크와 Docker 배포만 먼저
확인해야 할 때만 `llm_mode=mock`을 사용한다.

## 7. 첫 배포 순서

1. MacBook에서 Docker Desktop과 Tailscale 실행 확인
2. GitHub Secrets와 Variables 등록
3. GitHub Actions에서 `Deploy to MacBook Staging` workflow 수동 실행
4. 입력값은 기본값인 `llm_mode=litellm`, `publish_mode=local`로 시작
5. workflow의 test job 통과 확인
6. build_and_push job에서 GHCR image push 확인
7. deploy job에서 Tailscale ping, SSH upload, Docker Compose pull/up 확인
8. workflow 마지막 smoke test에서 `/v1/reviews` 성공 확인

MacBook에서 직접 확인:

```bash
cd /Users/Shared/ai-code-review-agent
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
docker compose exec postgres psql -U reviewer -d reviewer -c "select review_run_id, route_name, model_tier, created_at from review_runs order by created_at desc limit 5;"
ls -la .local-data/comments
```

다른 tailnet 기기에서 확인:

```bash
curl -fsS http://<macbook-tailscale-ip>:8080/healthz
```

## 8. 실제 PR 리뷰 테스트

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

## 9. 운영 모드 전환

배포 기본값:

```text
llm_mode=litellm
publish_mode=local
```

API key 없이 배포 통로만 확인:

```text
llm_mode=mock
publish_mode=local
```

실제 GitHub comment 확인:

```text
llm_mode=litellm
publish_mode=github
```

`publish_mode=github`는 staging API가 정상 동작하고 실제 PR comment 권한까지
확인할 때 사용한다.

## 10. 자주 막히는 지점

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
3. `MACBOOK_SSH_KEY`가 private key 원문 또는 private key를 base64로 인코딩한 값인지
4. private key에 대응하는 public key가 MacBook `authorized_keys`에 들어갔는지
5. private key에 passphrase가 없는지
6. Tailscale ACL에서 port 22 접근이 허용되는지

`Load key "...": error in libcrypto`가 나오면 대부분 private key secret이 줄바꿈 없이 깨졌거나, public key를 잘못 넣었거나, passphrase가 걸린 key를 넣은 경우다. `MACBOOK_SSH_KEY`에는 반드시 `macbook_staging_ed25519` private key 파일의 원문 또는 base64 인코딩 값을 넣어야 한다. `macbook_staging_ed25519.pub` 파일을 넣으면 안 된다.

### Docker 명령 실패

확인할 것:

1. Docker Desktop이 실행 중인지
2. SSH non-interactive 환경에서 `docker`가 PATH에 있는지
3. workflow는 `/Applications/Docker.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin`을 PATH에 추가한다.
4. `docker compose`가 없으면 workflow가 `docker-compose` fallback을 시도한다.

`keychain cannot be accessed because the current session does not allow user interaction`가 반복되면 SSH로 실행된 Docker가 MacBook 사용자 계정의 Docker credential helper를 계속 호출하는 상태다. 이 경우 workflow 우회보다 MacBook의 Docker config에서 Keychain helper 참조를 제거하는 것이 1순위 해결책이다.

MacBook에서 이 저장소를 받은 뒤 다음을 실행한다.

```bash
./infra/macbook-staging/fix-docker-credential-helper.sh
```

스크립트는 `~/.docker/config.json`을 백업하고 `credsStore`, `credHelpers` 항목만 제거한 뒤 public image pull을 테스트한다. staging 배포에서 앱 이미지는 workflow가 전달한 GHCR 임시 auth config로 pull하고, Docker Hub에서는 public image인 `pgvector/pgvector:pg16`만 pull하므로 Mac Keychain credential helper가 필요 없다.

수동으로 확인하려면 MacBook에서 다음을 본다.

```bash
cat ~/.docker/config.json
docker pull python:3.12-slim
docker pull pgvector/pgvector:pg16
```

`config.json`에 `"credsStore": "desktop"` 또는 `"credsStore": "osxkeychain"`이 남아 있고 SSH 배포에서 위 에러가 반복되면, 해당 helper가 원인이다.

### API는 뜨지만 smoke review 실패

확인할 것:

1. `AI_REVIEWER_TOKEN` secret과 요청 Authorization 값이 같은지
2. `docker compose logs api` 출력
3. `.env`가 MacBook 앱 디렉터리에 생성됐는지
4. `docker compose exec postgres pg_isready -U reviewer -d reviewer` 성공 여부

### GitHub comment가 안 달림

확인할 것:

1. `publish_mode=github`로 배포했는지
2. `STAGING_GITHUB_TOKEN`이 설정되어 있는지
3. token에 PR comment 권한이 있는지
4. private repository라면 token scope가 충분한지

## 11. 실제 서버 배포 전 체크리스트

MacBook staging에서 다음을 확인한 뒤 GCP 또는 실제 서버로 넘어간다.

1. Docker staging 배포 성공
2. `/healthz` 성공
3. sample review payload 성공
4. failure payload가 `solar3-low` review tier와 `low` 추론 강도로 라우팅
5. policy payload가 `solar3-medium` review tier와 `medium` 추론 강도로 라우팅
6. high-risk payload가 `solar3-high` review tier와 `high` 추론 강도로 라우팅
7. `llm_mode=litellm` 실제 Solar3 호출 성공
8. `publish_mode=github` 실제 PR comment 성공
9. 로그와 `review_runs` 테이블에서 실행 이력 확인
