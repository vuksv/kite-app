#!/usr/bin/env bash
# One-shot Google Cloud setup for the KiteLog archive service.  Needs the gcloud CLI
# (Cloud Shell in the browser works: https://shell.cloud.google.com - no laptop needed).
#   bash cloud/setup.sh <project-id> [bucket-name] [region]
# Prints the values to paste into GitHub > Settings > Secrets and variables > Actions.
set -euo pipefail
PROJECT=${1:?usage: setup.sh <project-id> [bucket-name] [region]}
BUCKET=${2:-${PROJECT}-kitelog}
REGION=${3:-us-central1}
gcloud config set project "$PROJECT" >/dev/null
echo "== enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  storage.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com
echo "== bucket gs://$BUCKET (private)"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access
echo "== upload token (Secret Manager: kitelog-token)"
if ! gcloud secrets describe kitelog-token >/dev/null 2>&1; then
  TOKEN=$(openssl rand -hex 16); printf '%s' "$TOKEN" | gcloud secrets create kitelog-token --data-file=-
else
  TOKEN=$(gcloud secrets versions access latest --secret=kitelog-token)
fi
echo "== service accounts"
RUN_SA="kitelog-run@$PROJECT.iam.gserviceaccount.com"
DEPLOY_SA="kitelog-deploy@$PROJECT.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$RUN_SA" >/dev/null 2>&1 || gcloud iam service-accounts create kitelog-run --display-name "KiteLog runtime"
gcloud iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1 || gcloud iam service-accounts create kitelog-deploy --display-name "KiteLog deployer"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$RUN_SA" --role=roles/storage.objectAdmin >/dev/null
gcloud secrets add-iam-policy-binding kitelog-token --member="serviceAccount:$RUN_SA" --role=roles/secretmanager.secretAccessor >/dev/null
for ROLE in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer roles/storage.admin roles/iam.serviceAccountUser roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$DEPLOY_SA" --role="$ROLE" >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA" --member="serviceAccount:$DEPLOY_SA" --role=roles/iam.serviceAccountUser >/dev/null
echo "== deployer key (quick start; delete the file after pasting it into GitHub)"
KEYFILE=$(mktemp)
gcloud iam service-accounts keys create "$KEYFILE" --iam-account="$DEPLOY_SA" >/dev/null
cat <<MSG

============================ paste into GitHub ============================
Settings > Secrets and variables > Actions > "Secrets" tab:
  GCP_PROJECT_ID = $PROJECT
  GCP_SA_KEY     = (contents of $KEYFILE - shown below)
"Variables" tab:
  GCP_REGION     = $REGION
  KITELOG_BUCKET = $BUCKET

Then: Actions > "Deploy to Cloud Run" > Run workflow.  The job prints the service URL.
On the phone open that URL, enter this token under "Cloud archive":
  KITELOG_TOKEN  = $TOKEN
===========================================================================
MSG
cat "$KEYFILE"; echo; echo "(key saved at $KEYFILE - run: rm $KEYFILE)"
