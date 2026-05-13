# Гайд разработчика — SubWise AI

## Что это за проект

Telegram-бот + веб-приложение для управления подписками. Пользователь пишет на естественном языке ("добавь Netflix 15 долларов") — AI понимает и выполняет.

## Структура проекта

```
subwise-ai/
├── functions/                    ← код бота (Cloud Functions)
│   ├── telegram_bot_webhook/     ← главная логика (AI + Telegram + Web)
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── handle_subscription_crud/ ← API для подписок
│   │   ├── main.py
│   │   └── requirements.txt
│   └── check_subscriptions_for_notification/ ← уведомления
│       ├── main.py
│       └── requirements.txt
├── web/                          ← PWA веб-приложение
│   ├── index.html
│   ├── manifest.json
│   └── sw.js
├── setup.sh                      ← установка с нуля
├── deploy.sh                     ← обновление функций
├── CONTRIBUTING.md               ← как работать вместе
├── DEVELOPER_GUIDE.md            ← этот файл
└── README.md                     ← описание для пользователей
```

## Как работает

1. Пользователь пишет в Telegram
2. Telegram шлёт webhook → Cloud Function `telegram_bot_webhook`
3. Функция отправляет текст в Gemini AI с промптом
4. Gemini возвращает JSON с действием (add/delete/list/advice/chat)
5. Функция выполняет действие (пишет в Firestore)
6. Отправляет ответ пользователю

## Как запустить локально (для тестирования)

```bash
# 1. Клонировать
git clone https://github.com/abuten1977-design/subwise-ai.git
cd subwise-ai

# 2. Установить зависимости
cd functions/telegram_bot_webhook
pip install -r requirements.txt
cd ../..

# 3. Для полного деплоя — нужен Google Cloud аккаунт
# Смотри setup.sh
```

## Как деплоить

```bash
# Обновить все функции в Google Cloud
bash deploy.sh
```

## Ключевые файлы для изменений

### Промпт AI (самое важное)
Файл: `functions/telegram_bot_webhook/main.py`
Переменная: `SYSTEM_PROMPT`
Здесь описано как AI должен понимать пользователя и что возвращать.

### Обработка действий
Файл: `functions/telegram_bot_webhook/main.py`
Функция: `_handle_ai_result()` — что делать с каждым action (add, delete, list...)

### Веб-интерфейс
Файл: `web/index.html` — весь PWA в одном файле

## Переменные окружения

| Переменная | Где | Что |
|-----------|-----|-----|
| GCP_PROJECT | Cloud Function env | ID проекта Google Cloud |
| asya-app-tg-bot-token | Secret Manager | Токен Telegram бота |
| gemini-api-key | Secret Manager | API ключ Gemini |

## Текущие проблемы

1. **Gemini Flash Lite плохо парсит подписки** — путает валюты, не понимает контекст
2. **Нет редактирования** — можно только добавить или удалить, не изменить
3. **Нет аналитики** — нет графиков расходов по месяцам

## Как тестировать

Напиши боту в Telegram:
- "добавь тест 100 долларов продление 15 мая" → должен добавить
- "какие подписки?" → должен показать список
- "удали тест" → должен удалить
- "что посоветуешь?" → должен проанализировать
