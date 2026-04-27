#!/bin/bash
# setup.sh — Развёртывание SubWise AI на Google Cloud
# Запусти: bash setup.sh

set -e

echo "=== SubWise AI — Установка ==="
echo ""

# 1. Спрашиваем данные
read -p "Google Cloud Project ID: " PROJECT_ID
read -p "Telegram Bot Token (от @BotFather): " BOT_TOKEN
read -p "Gemini API Key (от AI Studio): " API_KEY
read -p "Регион (по умолчанию us-central1): " REGION
REGION=${REGION:-us-central1}

echo ""
echo "Проект: $PROJECT_ID"
echo "Регион: $REGION"
echo ""
read -p "Всё верно? (y/n): " CONFIRM
[ "$CONFIRM" != "y" ] && echo "Отменено." && exit 1

# 2. Настраиваем gcloud
gcloud config set project "$PROJECT_ID"

# 3. Включаем API
echo "Включаю API..."
gcloud services enable cloudfunctions.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com 2>/dev/null

# 4. Создаём Firestore
echo "Создаю Firestore..."
gcloud firestore databases create --location="$REGION" --type=firestore-native 2>/dev/null || echo "Firestore уже существует"

# 5. Сохраняем секреты
echo "Сохраняю секреты..."
echo -n "$BOT_TOKEN" | gcloud secrets create asya-app-tg-bot-token --data-file=- 2>/dev/null || \
  echo -n "$BOT_TOKEN" | gcloud secrets versions add asya-app-tg-bot-token --data-file=-

echo -n "$API_KEY" | gcloud secrets create gemini-api-key --data-file=- 2>/dev/null || \
  echo -n "$API_KEY" | gcloud secrets versions add gemini-api-key --data-file=-

# 6. Деплоим функции
echo "Деплою функции..."

gcloud functions deploy telegram_bot_webhook \
  --gen2 --region="$REGION" --runtime=python312 \
  --trigger-http --allow-unauthenticated \
  --source=functions/telegram_bot_webhook \
  --entry-point=telegram_bot_webhook \
  --set-env-vars="GCP_PROJECT=$PROJECT_ID" \
  --memory=256MB --timeout=60s

gcloud functions deploy handle_subscription_crud \
  --gen2 --region="$REGION" --runtime=python312 \
  --trigger-http --allow-unauthenticated \
  --source=functions/handle_subscription_crud \
  --entry-point=handle_subscription_crud \
  --set-env-vars="GCP_PROJECT=$PROJECT_ID" \
  --memory=256MB --timeout=60s

gcloud functions deploy check_subscriptions_for_notification \
  --gen2 --region="$REGION" --runtime=python312 \
  --trigger-http --allow-unauthenticated \
  --source=functions/check_subscriptions_for_notification \
  --entry-point=check_subscriptions_for_notification \
  --set-env-vars="GCP_PROJECT=$PROJECT_ID" \
  --memory=256MB --timeout=60s

# 7. Получаем URL и ставим webhook
WEBHOOK_URL=$(gcloud functions describe telegram_bot_webhook --region="$REGION" --gen2 --format='value(serviceConfig.uri)')
echo "Webhook URL: $WEBHOOK_URL"

curl -s "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook?url=${WEBHOOK_URL}" | python3 -c "import json,sys; d=json.load(sys.stdin); print('Webhook:', 'OK' if d.get('ok') else d.get('description','ОШИБКА'))"

echo ""
echo "=== ✅ Готово! ==="
echo "Бот работает. Напиши ему /start в Telegram."
echo "Webhook: $WEBHOOK_URL"
