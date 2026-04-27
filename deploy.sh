#!/bin/bash
# deploy.sh — Обновление функций (после git pull)
set -e
REGION=${1:-us-central1}
PROJECT_ID=$(gcloud config get-value project)

echo "Обновляю функции в проекте $PROJECT_ID..."

for func in telegram_bot_webhook handle_subscription_crud check_subscriptions_for_notification; do
  echo "→ $func"
  gcloud functions deploy "$func" \
    --gen2 --region="$REGION" --runtime=python312 \
    --trigger-http --allow-unauthenticated \
    --source="functions/$func" \
    --entry-point="$func" \
    --set-env-vars="GCP_PROJECT=$PROJECT_ID" \
    --memory=256MB --timeout=60s
done

echo "✅ Все функции обновлены."
