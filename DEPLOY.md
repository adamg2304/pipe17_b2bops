# Auto-deploy from GitHub (Cloud Run Jobs)

Pushing to `main` now redeploys all three Cloud Run Jobs automatically via
`.github/workflows/deploy-cloud-run.yml`. No more manual `gcloud` from Cloud Shell.

- Track A `pipe17-airtable-sync`, Track B `hubspot-pipe17-orders`, and the inventory
  sync `pipe17-hubspot-inventory` all redeploy on any push to `main` that touches
  `*.py`, `Dockerfile`, `requirements.txt`, `assets/**`, or the workflow itself.
- You can also deploy on demand: repo → **Actions** → **Deploy Cloud Run Jobs** →
  **Run workflow**, and pick `both`, `track-a`, `track-b`, or `inventory`.
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
# Wait for the new account to propagate before binding roles to it, so the
# first binding doesn't race ahead with a "does not exist" error.
until gcloud iam service-accounts describe "$SA" --project "$PROJECT_ID" >/dev/null 2>&1; do
  echo "waiting for $SA to propagate..."; sleep 5
done
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

## Inventory sync job (pipe17-hubspot-inventory)

Pushes current Pipe17 **Available** into the HubSpot product library
(`inventory_us` / `inventory_ca` / `inventory_total`) so Sales/Loncom see live
stock when quoting. Read-only from Pipe17; writes only HubSpot product properties.
Touches no deals, orders, or the order/shipment sync.

- **First deploy ships `DRY_RUN=true`** (set in the workflow). It logs the computed
  per-product US/CA/total and writes nothing. Confirm the numbers in the logs, then
  flip to live by changing `--set-env-vars=DRY_RUN=true` → `DRY_RUN=false` for the
  inventory step in `deploy-cloud-run.yml` and pushing.
- Spot-check one SKU without scanning the whole catalog: **Actions → Run Cloud Run
  Job → `pipe17-hubspot-inventory`**, args `main_inventory_sync.py,--sku,11-01-00-40`.
- Region split: each Pipe17 location is classified US/CA by its `address.country`
  (read live from `/locations`), so new warehouses need no code change. A base SKU's
  availability sums across its whole version family (bare base + every `-V` variant),
  bucketed purely by warehouse country — so stock on any version counts. The channel
  Channel-SKU alias is NOT used here (it only governs which version an order draws).
  `inventory_total = inventory_us + inventory_ca` (MX/BR excluded).

### Scheduler (every 20 min)

The two existing jobs are driven by Cloud Scheduler entries that call the Cloud Run
Jobs run API with an OIDC token from the invoker SA. Add one for the inventory job
the same way (run once; reuse the invoker SA the other two schedulers already use):

```bash
PROJECT_ID=pipe17-b2bops
REGION=europe-west1
PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
INVOKER_SA=$(gcloud scheduler jobs describe pipe17-airtable-sync-schedule \
  --location "$REGION" --project "$PROJECT_ID" \
  --format='value(httpTarget.oidcToken.serviceAccountEmail)')   # same SA as Track A/B

gcloud scheduler jobs create http pipe17-hubspot-inventory-schedule \
  --project "$PROJECT_ID" --location "$REGION" \
  --schedule="*/20 * * * *" --time-zone="Etc/UTC" \
  --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_NUM/jobs/pipe17-hubspot-inventory:run" \
  --http-method=POST \
  --oauth-service-account-email="$INVOKER_SA" \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
```

(If the existing schedulers use `--oidc-*` flags instead, mirror those — the point is
the same invoker SA and a `*/20 * * * *` cadence.)

## SKU resolution needs a catalog-scoped Pipe17 key

Track B translates each HubSpot base SKU to the Pipe17 product SKU by reading the
product catalog (`GET /products`) via `pipe17_catalog`. The B2B **order-channel**
keys (`pipe17-api-key-us` / `-ca`) are **not** authorized for `/products` (they
return 403), so resolution needs a dedicated catalog-scoped key.

- Until one is configured, the resolver **fails open**: it logs a warning and sends
  each line out with its base SKU unchanged (the pre-resolution behaviour), so order
  creation keeps working — versioned items just won't be auto-translated.
- To enable versioned resolution: store the catalog-scoped key as a secret (e.g.
  `pipe17-catalog-key`) and add it to the **Track B** deploy step in
  `deploy-cloud-run.yml`:

  ```
  --set-secrets=...,PIPE17_CATALOG_API_KEY=pipe17-catalog-key:latest
  ```

  Also grant the deployer SA `secretAccessor` on it (the `for S in ...` loop in the
  one-time setup). The inventory job does **not** need this key — it reads
  `/inventory` + `/locations` only.

## Verifying / troubleshooting

- Watch the run under the repo's **Actions** tab; the deploy step streams the same
  `gcloud run jobs deploy` output you'd see in Cloud Shell.
- `PERMISSION_DENIED` on deploy → a role in step 2 is missing on the deployer SA.
- `Permission denied on secret` → step 3 didn't cover that secret name.
- Auth step fails with an OIDC/subject error → the `attribute-condition` repo string
  in step 4 must exactly match `owner/repo` (case-sensitive).
- Reading job logs afterwards is unchanged (handoff §7):
  `gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="<job>"' --project pipe17-b2bops --limit 40 --freshness=15m --format='value(timestamp,textPayload)' --order=desc`
