# GCP Deployment Notes

Target runtime: Cloud Run.

Required GCP resources:

1. Artifact Registry Docker repository
2. Cloud Run service
3. Secret Manager secrets for `AI_REVIEWER_TOKEN` and `UPSTAGE_API_KEY`
4. Workload Identity Federation provider for GitHub Actions
5. Service account with Cloud Run deploy and Artifact Registry write permissions

Recommended GitHub repository variables:

```text
GCP_PROJECT_ID=<project-id>
GCP_REGION=asia-northeast3
ARTIFACT_REPOSITORY=ai-reviewer
CLOUD_RUN_SERVICE=ai-code-review-agent
```

Recommended GitHub repository secrets:

```text
GCP_WORKLOAD_IDENTITY_PROVIDER=projects/.../providers/...
GCP_SERVICE_ACCOUNT=github-deployer@<project-id>.iam.gserviceaccount.com
```

