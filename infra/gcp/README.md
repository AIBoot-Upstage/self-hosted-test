# GCP Deployment Notes

Target runtime: Cloud Run.

현재 GCP 배포 workflow는 `workflow_dispatch` 수동 실행 전용이다. 로컬 배포 테스트가 끝난 뒤 GitHub Actions에서 `Deploy to GCP Cloud Run`을 직접 실행한다.

Required GCP resources:

1. Artifact Registry Docker repository
2. Cloud Run service
3. Secret Manager secrets for `AI_REVIEWER_TOKEN`, `UPSTAGE_API_KEY`, `GITHUB_WEBHOOK_SECRET`, and `GITHUB_APP_PRIVATE_KEY`
4. Workload Identity Federation provider for GitHub Actions
5. Service account with Cloud Run deploy and Artifact Registry write permissions
6. GitHub App installed on the target repository or organization

Recommended GitHub repository variables:

```text
GCP_PROJECT_ID=<project-id>
GCP_REGION=asia-northeast3
ARTIFACT_REPOSITORY=ai-reviewer
CLOUD_RUN_SERVICE=ai-code-review-agent
GITHUB_APP_ID=<github-app-id>
GITHUB_WEBHOOK_REVIEW_MODE=after_checks
```

Recommended GitHub repository secrets:

```text
GCP_WORKLOAD_IDENTITY_PROVIDER=projects/.../providers/...
GCP_SERVICE_ACCOUNT=github-deployer@<project-id>.iam.gserviceaccount.com
```

GitHub App setup:

1. Register a GitHub App owned by the target organization or user.
2. Set webhook URL to `https://<cloud-run-url>/v1/github/webhooks`.
3. Generate a high-entropy webhook secret and save it to Secret Manager as `GITHUB_WEBHOOK_SECRET`.
4. Generate a private key and save the PEM content to Secret Manager as `GITHUB_APP_PRIVATE_KEY`.
5. Configure repository permissions:
   - `Contents: Read`
   - `Pull requests: Read and write`
   - `Checks: Read`
   - `Metadata: Read`
6. Subscribe to events:
   - `pull_request`
   - `check_suite`
   - `installation`
   - `installation_repositories`

With `GITHUB_WEBHOOK_REVIEW_MODE=after_checks`, review execution starts from
`check_suite.completed`. Use `pull_request` mode only for repositories without CI.
