import functions_framework
from google.cloud import firestore
from google.cloud import secretmanager
import os
import json
import httpx
import time
import random
import string

db = firestore.Client()
secret_client = secretmanager.SecretManagerServiceClient()

_cache = {}
MAX_HISTORY = 10

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

MODELS = [
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]

def _get_secret(secret_id, version_id="latest"):
    if secret_id not in _cache:
        project_id = os.environ.get('GCP_PROJECT', 'autofriend-app-1773430739')
        name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
        response = secret_client.access_secret_version(request={"name": name})
        _cache[secret_id] = response.payload.data.decode("UTF-8").strip()
    return _cache[secret_id]

def _generate_web_code(chat_id):
    """Generate 6-digit code for web auth, store in Firestore with 5 min TTL."""
    code = ''.join(random.choices(string.digits, k=6))
    db.collection("web_auth_codes").document(code).set({
        "chat_id": str(chat_id),
        "created_at": time.time(),
    })
    return code

def _verify_web_code(code):
    """Verify code and return chat_id if valid (< 5 min old)."""
    doc = db.collection("web_auth_codes").document(code).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    if time.time() - data.get("created_at", 0) > 300:
        db.collection("web_auth_codes").document(code).delete()
        return None
    db.collection("web_auth_codes").document(code).delete()
    return data.get("chat_id")

def _send_message(chat_id, text):
    token = _get_secret("asya-app-tg-bot-token")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = httpx.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    resp.raise_for_status()

def _user_subs(user_id):
    return db.collection("users").document(str(user_id)).collection("subscriptions")

def _get_subscriptions(user_id):
    return [doc.to_dict() for doc in _user_subs(user_id).stream()]

def _format_subs(subs):
    if not subs:
        return "Подписок пока нет."
    lines = [f"Подписки ({len(subs)}):"]
    for s in subs:
        cur = s.get("currency", "$")
        lines.append(f"- {s['name']} ({s['cost']:.2f} {cur}) {s.get('renewal_date', '')}")
    return "\n".join(lines)

def _get_history(chat_id):
    doc = db.collection("chat_history").document(str(chat_id)).get()
    if doc.exists:
        return doc.to_dict().get("messages", [])
    return []

def _save_history(chat_id, messages):
    db.collection("chat_history").document(str(chat_id)).set({"messages": messages[-MAX_HISTORY:]})

def _append_history(chat_id, role, text):
    history = _get_history(chat_id)
    history.append({"role": role, "text": text})
    _save_history(chat_id, history)
    return history

SYSTEM_PROMPT = """Ты — умный ассистент для управления подписками. Тебя зовут Ася-бот.
Пользователь общается с тобой на естественном языке. Ты должен понять намерение и вернуть JSON.

Текущие подписки пользователя:
{subs}

Верни ТОЛЬКО валидный JSON (без markdown, без ```), одно из:
- {{"action":"add","name":"...","cost":число,"currency":"...","renewal_date":"YYYY-MM-DD","reply":"..."}}
- {{"action":"delete","name":"...","reply":"..."}}
- {{"action":"list","reply":"..."}}
- {{"action":"advice","reply":"..."}}
- {{"action":"chat","reply":"..."}}

Правила:
- У пользователя подписки в РАЗНЫХ валютах (доллары, кроны, гривны и др.)
- currency — сохраняй как пользователь сказал: "$", "Kč", "грн", "€" и т.д.
- Если пользователь не указал валюту — спроси в reply, action="chat".
- Если хочет добавить — извлеки name, cost, currency, renewal_date. Если чего-то не хватает — спроси.
- Если хочет удалить — извлеки name.
- Если спрашивает список — action="list", перечисли каждую подписку с её валютой.
- Если просит совет — action="advice", проанализируй.
- Для обычного разговора — action="chat".
- reply на русском, дружелюбно и кратко.
- Учитывай историю разговора."""

def _extract_text(resp_json):
    try:
        parts = resp_json["candidates"][0]["content"]["parts"]
        for part in parts:
            if "text" in part:
                return part["text"]
    except (KeyError, IndexError):
        pass
    return None

def _ask_ai(user_text, subs, history):
    api_key = _get_secret("gemini-api-key")

    contents = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["text"]}]})

    body = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT.format(subs=_format_subs(subs))}]},
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024},
    }

    last_error = None
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        try:
            resp = httpx.post(url, json=body, timeout=25)
            if resp.status_code in (429, 503):
                print(f"{model} -> {resp.status_code}, skipping")
                continue
            resp.raise_for_status()
            raw = _extract_text(resp.json())
            if not raw:
                print(f"{model} -> empty response, skipping")
                continue
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()
            return json.loads(raw)
        except Exception as e:
            last_error = e
            print(f"{model} -> error: {e}")
            continue

    raise last_error or Exception("All models failed")

def _handle_ai_result(result, user_id):
    """Execute AI action and return reply."""
    action = result.get("action", "chat")
    reply = result.get("reply", "")
    col = _user_subs(user_id)

    if action == "add":
        col.document(result["name"]).set({
            "name": result["name"],
            "cost": float(result["cost"]),
            "currency": result.get("currency", "$"),
            "renewal_date": result["renewal_date"],
            "created_at": firestore.SERVER_TIMESTAMP,
        })
        reply = f"✅ {reply or result['name'] + ' добавлена.'}"
    elif action == "delete":
        col.document(result["name"]).delete()
        reply = f"🗑 {reply or result['name'] + ' удалена.'}"
    elif action == "list":
        reply = reply or _format_subs(_get_subscriptions(user_id))
    else:
        reply = reply or "Не понял, попробуй ещё раз."

    return action, reply

def _handle_ai_tg(chat_id, text):
    subs = _get_subscriptions(chat_id)
    history = _append_history(chat_id, "user", text)
    try:
        result = _ask_ai(text, subs, history)
    except Exception as e:
        print(f"AI error (all models): {e}")
        _append_history(chat_id, "bot", "⏳ AI временно недоступен, попробуй через минуту.")
        _send_message(chat_id, "⏳ AI временно недоступен, попробуй через минуту.")
        return
    action, reply = _handle_ai_result(result, chat_id)
    _append_history(chat_id, "bot", reply)
    _send_message(chat_id, reply)

def _handle_web_chat(text, user_id):
    """Handle AI chat from web — no Telegram, return JSON response."""
    subs = _get_subscriptions(user_id)
    history = _append_history(user_id, "user", text)
    try:
        result = _ask_ai(text, subs, history)
    except Exception as e:
        print(f"Web AI error: {e}")
        return {"action": "error", "reply": "⏳ AI временно недоступен, попробуй через минуту."}
    action, reply = _handle_ai_result(result, user_id)
    _append_history(user_id, "bot", reply)
    return {"action": action, "reply": reply}

@functions_framework.http
def telegram_bot_webhook(request):
    # CORS preflight
    if request.method == "OPTIONS":
        return "", 204, CORS_HEADERS

    try:
        data = request.get_json(force=True)

        # Web auth verify
        if data.get("verify_code"):
            code = data.get("code", "").strip()
            chat_id = _verify_web_code(code)
            if chat_id:
                return json.dumps({"ok": True, "user_id": chat_id}), 200, {**CORS_HEADERS, "Content-Type": "application/json"}
            return json.dumps({"ok": False, "error": "Неверный или просроченный код"}), 200, {**CORS_HEADERS, "Content-Type": "application/json"}

        # Web chat request
        if data.get("web_chat"):
            text = data.get("text", "").strip()
            user_id = data.get("user_id", "web")
            if not text:
                return json.dumps({"reply": "Пустое сообщение"}), 200, {**CORS_HEADERS, "Content-Type": "application/json"}
            result = _handle_web_chat(text, user_id)
            return json.dumps(result, ensure_ascii=False), 200, {**CORS_HEADERS, "Content-Type": "application/json"}

        # Telegram webhook
        message = data.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")

        if not chat_id or not text:
            return "OK", 200

        if text == "/start":
            _send_message(chat_id, f"Привет! Я Ася-бот 🤖\nТвой ID: {chat_id}\n\nПиши мне как обычно:\n• «добавь Netflix 5 долларов, продление 1 мая»\n• «добавь Spotify 149 крон, продление 15 мая»\n• «какие у меня подписки?»\n• «что посоветуешь удалить?»\n\nИли команды: /list /add /delete /web")
        elif text == "/web":
            code = _generate_web_code(chat_id)
            _send_message(chat_id, f"🔑 Код для входа в веб-приложение:\n\n<code>{code}</code>\n\nВведи его на сайте. Код действует 5 минут.")
        elif text == "/list":
            subs = _get_subscriptions(chat_id)
            _send_message(chat_id, _format_subs(subs))
        elif text.startswith("/add "):
            parts = text.split(maxsplit=3)
            if len(parts) < 4:
                _send_message(chat_id, "Формат: /add имя цена дата\nИли просто напиши: добавь Netflix 5 долларов 1 мая")
            else:
                _user_subs(chat_id).document(parts[1]).set({
                    "name": parts[1], "cost": float(parts[2]),
                    "renewal_date": parts[3], "created_at": firestore.SERVER_TIMESTAMP,
                })
                _send_message(chat_id, f"✅ '{parts[1]}' добавлена.")
        elif text.startswith("/delete "):
            name = text.split(maxsplit=1)[1]
            _user_subs(chat_id).document(name).delete()
            _send_message(chat_id, f"🗑 '{name}' удалена.")
        else:
            _handle_ai_tg(chat_id, text)

        return "OK", 200
    except Exception as e:
        print(f"ERROR in webhook: {e}")
        return "OK", 200
