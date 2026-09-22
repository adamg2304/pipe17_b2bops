# Auto-deploy from GitHub (Cloud Run Jobs)

Pushing to `main` now redeploys both Cloud Run Jobs automatically via
`.github/workflows/deploy-cloud-run.yml`. No more manual `gcloud` from Cloud Shell.

- Track A `pipe17-airtable-sync` and Track B `hubspot-pipe17-orders` both redeploy
  on any push to `main` that touches `*.py`, `Dockerfile`, `requirements.txt`,
  `assets/**`, or the workflow itself.
- You can also deploy on demand: repo → **Actions** → **Deploy Cloud Run Jobs** →
  **Run workflow**, and pick `both`, `track-a`, or `track-b`.
- The env vars and `--set-secrets` in the workflow ARE the canonical job config
  (from the handoff §7). Each deploy replaces the job's env/secrets with exactly
  those values, so change config by editing the workflow, not the job in the console
  (a console edit is overwritten on the next deploy).
- Schedulers are untouched — they invoke whatever the latest deployed job is, so the
  30-min cadence keeps working across deploys.

## One-time setup (run once, in Cloud Shell / any authed gcloud)

This uses **Workload Identity Federation**, so GitHub authenticates to GCP with a
short-lived token and there is no service-account key to store or rotate. Run it
once as a project owner. Copy-paste block:

```bash
PROJECT_ID=pipe17-b2bops
REPO=adamg2304/pipe17_b2bops          # owner/repo
PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
SA=gh-deployer@$PROJECT_ID.iam.gserviceaccount.com

# 1. Enable the APIs the deploy needs.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com iamcredentials.googleapis.com \
  secretmanager.googleapis.com --project "$PROJECT_ID"

# 2. Deployer service account + the roles a --source deploy needs.
gcloud iam service-accounts create gh-deployer --project "$PROJECT_ID" \
  --display-name "GitHub Actions Cloud Run deployer" || true
for ROLE in roles/run.admin roles/cloudbuild.builds.editor \
            roles/artifactregistry.writer roles/storage.admin \
            roles/iam.serviceAccountUser roles/logging.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$SA" --role "$ROLE" --condition=None
done

# 3. Let the deployer read the 5 secrets the jobs reference.
for S in pipe17-api-key airtable-pat pipe17-api-key-us pipe17-api-key-ca hubspot-token; do
  gcloud secrets add-iam-policy-binding "$S" --project "$PROJECT_ID" \
    --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor
done

# 4. Workload Identity pool + GitHub OIDC provider (locked to this one repo).
gcloud iam workload-identity-pools create github-pool \
  --project "$PROJECT_ID" --location global --display-name "GitHub Actions" || true
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --project "$PROJECT_ID" --location global \
  --workload-identity-pool github-pool \
  --display-name "GitHub OIDC" \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition "assertion.repository=='$REPO'" || true

# 5. Allow that repo's tokens to impersonate the deployer SA.
POOL=projects/$PROJECT_NUM/locations/global/workloadIdentityPools/github-pool
gcloud iam service-accounts add-iam-policy-binding "$SA" --project "$PROJECT_ID" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/$POOL/attribute.repository/$REPO"

# 6. Print the two values you paste into GitHub secrets.
echo "GCP_WORKLOAD_IDENTITY_PROVIDER=$POOL/providers/github-provider"
echo "GCP_DEPLOY_SERVICE_ACCOUNT=$SA"
```

Then in GitHub: repo → **Settings → Secrets and variables → Actions → New repository
secret**, add both values printed by step 6:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_DEPLOY_SERVICE_ACCOUNT`

That's it. The next push to `main` (or a manual **Run workflow**) deploys.

## Verifying / troubleshooting

- Watch the run under the repo's **Actions** tab; the deploy step streams the same
  `gcloud run jobs deploy` output you'd see in Cloud Shell.
- `PERMISSION_DENIED` on deploy → a role in step 2 is missing on the deployer SA.
- `Permission denied on secret` → step 3 didn't cover that secret name.
- Auth step fails with an OIDC/subject error → the `attribute-condition` repo string
  in step 4 must exactly match `owner/repo` (case-sensitive).
- Reading job logs afterwards is unchanged (handoff §7):
  `gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="<job>"' --project pipe17-b2bops --limit 40 --freshness=15m --format='value(timestamp,textPayload)' --order=desc`
