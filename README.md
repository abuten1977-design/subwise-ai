# SubWise AI — Менеджер подписок

Telegram-бот с AI для управления подписками. Говоришь на естественном языке — бот понимает и выполняет.

## Быстрый старт

### Что нужно заранее
1. Google Cloud аккаунт с биллингом
2. `gcloud` CLI ([установка](https://cloud.google.com/sdk/docs/install))
3. Telegram бот от @BotFather (получишь токен)
4. Gemini API ключ от [AI Studio](https://aistudio.google.com/apikey)

### Установка (одна команда)

```bash
git clone https://github.com/abuten1977-design/subwise-ai.git
cd subwise-ai
bash setup.sh
```

Скрипт спросит Project ID, токен бота и API ключ. Всё остальное сделает сам.

### Обновление (после изменений в репо)

```bash
git pull
bash deploy.sh
```

## Что умеет бот

- "добавь Netflix 15 долларов, продление 5 мая" → сохраняет
- "какие у меня подписки?" → показывает список
- "удали Spotify" → удаляет
- "что посоветуешь?" → AI анализирует и рекомендует
- `/web` → код для входа в веб-приложение
- Мульти-валюта: $, €, Kč, грн

## Структура

```
subwise-ai/
├── setup.sh              ← первая установка
├── deploy.sh             ← обновление функций
├── functions/
│   ├── telegram_bot_webhook/
│   ├── handle_subscription_crud/
│   └── check_subscriptions_for_notification/
└── web/                  ← PWA интерфейс
```

## Технологии

- Google Cloud Functions (Python 3.12)
- Google Firestore
- Gemini API (3.1 Flash Lite)
- Telegram Bot API
