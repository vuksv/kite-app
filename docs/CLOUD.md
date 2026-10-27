# Cloud setup (optional)

The report app needs no server: `web/` runs entirely in the browser, and GitHub Pages
hosts it for free (`.github/workflows/pages.yml`, URL `https://vuksv.github.io/kite-app/`
after *Settings › Pages › Source = GitHub Actions*). **Start there.**

Use Google Cloud when you want an archive of every session (FIT + results) that you can
reach from any device, and the Python pipeline running server-side (plots, batch
re-analysis after a threshold change). The service in `cloud/` does that:

* `POST /api/analyze` – upload `.fit` / Garmin `.zip` / `.fit.gz`, get JSON back, file and
  results archived under `sessions/<date>_<hash>/` in a bucket;
* `GET /api/sessions`, `GET /api/sessions/<id>/<file>` – browse the archive;
* `/` – the same report app, with the "Upload to cloud archive" button pre-wired.

## Recommended free-tier shape

| piece | service | always-free allowance (check the pricing pages, they move) |
|---|---|---|
| compute | **Cloud Run** (request-based, `min-instances 0`) | 2 M requests, 180 k vCPU-s, 360 k GiB-s per month |
| storage | **Cloud Storage** standard bucket in `us-central1` / `us-east1` / `us-west1` | 5 GB-months |
| build | Cloud Build (`gcloud run deploy --source`) + Artifact Registry | 120 build-min/day, 0.5 GB images |
| secret | Secret Manager (`kitelog-token`) | 6 secret versions |

A kite session is ~0.6 MB per hour, one upload per session, so this stays inside the free
tier indefinitely. Keep the region at `us-central1` (the storage allowance is US-only) and
`max-instances 2` as a cost guard. Cloud Run functions would also work; the container keeps
the exact same code as `tools/`, which is simpler.

## One-time Google Cloud setup

Fastest: open [Cloud Shell](https://shell.cloud.google.com) (works from the phone browser),
clone the repo and run `bash cloud/setup.sh <project-id>`; it performs everything below and
prints the GitHub secrets/variables and the upload token. Manual steps for reference:

```bash
PROJECT=<your-project-id>; REGION=us-central1; BUCKET=<globally-unique-bucket-name>
gcloud config set project $PROJECT
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    storage.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com

# archive bucket (private)
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access

# shared upload token (any random string); the service reads it from Secret Manager
openssl rand -hex 24 | gcloud secrets create kitelog-token --data-file=-

# runtime service account (what the Cloud Run service runs as)
gcloud iam service-accounts create kitelog-run --display-name "KiteLog runtime"
RUN_SA=kitelog-run@$PROJECT.iam.gserviceaccount.com
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$RUN_SA --role=roles/storage.objectAdmin
gcloud secrets add-iam-policy-binding kitelog-token --member=serviceAccount:$RUN_SA --role=roles/secretmanager.secretAccessor

# deployer service account (what GitHub Actions uses)
gcloud iam service-accounts create kitelog-deploy --display-name "KiteLog deployer"
DEPLOY_SA=kitelog-deploy@$PROJECT.iam.gserviceaccount.com
for ROLE in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer roles/storage.admin roles/iam.serviceAccountUser roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$DEPLOY_SA --role=$ROLE
done
```

The first `gcloud run deploy --source` creates the Artifact Registry repository
`cloud-run-source-deploy` automatically. Add `--service-account $RUN_SA` to the deploy
command in the workflow if you want the service to run as `kitelog-run` instead of the
default compute account (recommended; then give `kitelog-deploy` `roles/iam.serviceAccountUser`
on `kitelog-run`).

## GitHub secrets and variables

Repository › Settings › Secrets and variables › Actions.

**Secrets**

| name | value |
|---|---|
| `GCP_PROJECT_ID` | the project id |
| `GCP_SA_KEY` | *quick start:* JSON key of `kitelog-deploy` (`gcloud iam service-accounts keys create key.json --iam-account=$DEPLOY_SA`, paste the file, delete it locally) |
| `GCP_WIF_PROVIDER` + `GCP_SA_EMAIL` | *recommended instead of a key:* Workload Identity Federation provider resource name and `$DEPLOY_SA` (setup below); leave `GCP_SA_KEY` empty |

**Variables**

| name | value |
|---|---|
| `GCP_REGION` | `us-central1` |
| `KITELOG_BUCKET` | `$BUCKET` |
| `KITELOG_SERVICE` | `kitelog` (optional) |

Workload Identity Federation (no long-lived key in GitHub):

```bash
gcloud iam workload-identity-pools create github --location=global --display-name=GitHub
gcloud iam workload-identity-pools providers create-oidc github-oidc --location=global --workload-identity-pool=github \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='vuksv/kite-app'"
PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
gcloud iam service-accounts add-iam-policy-binding $DEPLOY_SA --role=roles/iam.workloadIdentityUser \
    --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/vuksv/kite-app"
# GCP_WIF_PROVIDER = projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-oidc
```

Push to `main` (or run the workflow manually) and the job prints the service URL.

## Using it

* Open the service URL on the phone: it is the report app. In *Analysis settings › Cloud
  archive* enter the URL and the token once (stored in the browser), then **Upload to cloud
  archive** after analysing a file.
* `curl -F file=@activity.fit -H "X-KiteLog-Token: $TOKEN" https://<service>/api/analyze`
  from anywhere.
* Browse `https://<service>/api/sessions?token=...`.

## Local run (no Google account needed)

```bash
pip install -r cloud/requirements.txt
uvicorn cloud.main:app --reload --port 8080       # archive goes to ./data/
curl -F file=@web/sample/synthetic.fit localhost:8080/api/analyze | head -c 400
```
