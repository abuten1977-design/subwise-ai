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

# Vertex AI Search config
PROJECT_ID = os.environ.get('GCP_PROJECT', 'autofriend-app-1773430739')
SEARCH_ENGINE = f"projects/{PROJECT_ID}/locations/global/collections/default_collection/engines/subscriptions-engine"
SEARCH_DATASTORE = f"projects/{PROJECT_ID}/locations/global/collections/default_collection/dataStores/subscriptions-store"

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

def _user_savings(user_id):
    return db.collection("users").document(str(user_id)).collection("savings")

def _get_subscriptions(user_id):
    return [doc.to_dict() for doc in _user_subs(user_id).stream()]

def _get_savings(user_id):
    return [doc.to_dict() for doc in _user_savings(user_id).stream()]

def _format_subs(subs):
    if not subs:
        return "Подписок пока нет."
    lines = [f"Подписки ({len(subs)}):"]
    for s in subs:
        cur = s.get("currency", "$")
        lines.append(f"- {s['name']} ({s['cost']:.2f} {cur}) {s.get('renewal_date', '')}")
    return "\n".join(lines)

def _format_savings(savings):
    if not savings:
        return "Накоплений пока нет."
    lines = [f"Накопления ({len(savings)}):"]
    for s in savings:
        cur = s.get("currency", "$")
        saved = s.get("saved", 0)
        goal = s.get("goal", 0)
        pct = int(saved / goal * 100) if goal > 0 else 0
        remain = goal - saved
        lines.append(f"- {s['name']}: {saved:.0f}/{goal:.0f} {cur} ({pct}%) взнос {s.get('monthly', 0):.0f} {cur} {s.get('day', '?')}-го числа. Осталось {remain:.0f} {cur}")
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

SYSTEM_PROMPT = """Ты — умный ассистент для управления подписками и накоплениями. Тебя зовут Ася-бот.
Пользователь общается с тобой на естественном языке. Ты должен понять намерение и вернуть JSON.

Сегодня: {today}

Текущие подписки пользователя:
{subs}

Текущие накопления пользователя:
{savings}

Верни ТОЛЬКО валидный JSON (без markdown, без ```), одно из:
- {{"action":"add","name":"...","cost":число,"currency":"...","renewal_date":"YYYY-MM-DD","reply":"..."}}
- {{"action":"delete","name":"...","reply":"..."}}
- {{"action":"list","reply":"..."}}
- {{"action":"advice","reply":"..."}}
- {{"action":"savings_add","name":"...","goal":число,"monthly":число,"currency":"...","day":число,"reply":"..."}}
- {{"action":"savings_deposit","name":"...","amount":число,"reply":"..."}}
- {{"action":"savings_list","reply":"..."}}
- {{"action":"savings_delete","name":"...","reply":"..."}}
- {{"action":"chat","reply":"..."}}

Правила:
- У пользователя подписки в РАЗНЫХ валютах (доллары, кроны, гривны и др.)
- currency — сохраняй как пользователь сказал: "$", "Kč", "грн", "€" и т.д.
- Если пользователь не указал валюту — спроси в reply, action="chat".
- Если хочет добавить подписку — извлеки name, cost, currency, renewal_date. Если чего-то не хватает — спроси.
- Если хочет удалить — извлеки name.
- Если спрашивает список — action="list", перечисли подписки. Если просит накопления — action="savings_list".
- Если просит совет — action="advice", проанализируй подписки И накопления.
- Накопление: "коплю на машину 5000 долларов по 100 каждое 15 число" → savings_add.
- Внесение: "внёс 100 на машину" → savings_deposit.
- В reply для накоплений показывай прогресс: сколько из скольки, процент, сколько осталось.
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

def _ask_ai(user_text, subs, savings, history):
    api_key = _get_secret("gemini-api-key")

    contents = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["text"]}]})

    body = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT.format(subs=_format_subs(subs), savings=_format_savings(savings), today=time.strftime("%Y-%m-%d, %A"))}]},
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
    sav = _user_savings(user_id)

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
    elif action == "savings_add":
        sav.document(result["name"]).set({
            "name": result["name"],
            "goal": float(result["goal"]),
            "saved": 0,
            "monthly": float(result.get("monthly", 0)),
            "currency": result.get("currency", "$"),
            "day": int(result.get("day", 1)),
            "created_at": firestore.SERVER_TIMESTAMP,
        })
        reply = f"🎯 {reply or result['name'] + ' создано.'}"
    elif action == "savings_deposit":
        doc = sav.document(result["name"]).get()
        if doc.exists:
            data = doc.to_dict()
            new_saved = data.get("saved", 0) + float(result["amount"])
            sav.document(result["name"]).update({"saved": new_saved})
            goal = data.get("goal", 0)
            cur = data.get("currency", "$")
            pct = int(new_saved / goal * 100) if goal > 0 else 0
            remain = goal - new_saved
            reply = reply or f"✅ Внесено. {result['name']}: {new_saved:.0f}/{goal:.0f} {cur} ({pct}%). Осталось {remain:.0f} {cur}"
        else:
            reply = f"❌ Накопление '{result['name']}' не найдено."
    elif action == "savings_list":
        reply = reply or _format_savings(_get_savings(user_id))
    elif action == "savings_delete":
        sav.document(result["name"]).delete()
        reply = f"🗑 {reply or result['name'] + ' удалено.'}"
    else:
        reply = reply or "Не понял, попробуй ещё раз."

    return action, reply

def _handle_ai_tg(chat_id, text):
    subs = _get_subscriptions(chat_id)
    savings = _get_savings(chat_id)
    history = _append_history(chat_id, "user", text)
    try:
        result = _ask_ai(text, subs, savings, history)
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
    savings = _get_savings(user_id)
    history = _append_history(user_id, "user", text)
    try:
        result = _ask_ai(text, subs, savings, history)
    except Exception as e:
        print(f"Web AI error: {e}")
        return {"action": "error", "reply": "⏳ AI временно недоступен, попробуй через минуту."}
    action, reply = _handle_ai_result(result, user_id)
    _append_history(user_id, "bot", reply)
    return {"action": action, "reply": reply}

# === V2: Vertex AI Search ===

def _get_user_mode(user_id):
    doc = db.collection("user_settings").document(str(user_id)).get()
    if doc.exists:
        return doc.to_dict().get("mode", "v1")
    return "v1"

def _set_user_mode(user_id, mode):
    db.collection("user_settings").document(str(user_id)).set({"mode": mode}, merge=True)

def _sync_sub_to_search(user_id, sub_data):
    """Синхронизирует подписку в Vertex AI Search data store."""
    try:
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
        token = creds.token

        doc_id = f"{user_id}_{sub_data['name']}".replace(" ", "_").replace("/", "_")
        import base64
        text = f"{sub_data.get('name','')} {sub_data.get('cost',0)} {sub_data.get('currency','$')} renewal {sub_data.get('renewal_date','')} user {user_id}"
        url = f"https://discoveryengine.googleapis.com/v1/{SEARCH_DATASTORE}/branches/default_branch/documents?documentId={doc_id}"
        resp = httpx.post(url, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-goog-user-project": PROJECT_ID
        }, json={
            "id": doc_id,
            "structData": {**sub_data, "user": str(user_id)},
            "content": {"mimeType": "text/plain", "rawBytes": base64.b64encode(text.encode()).decode()}
        }, timeout=10)
        print(f"Search sync: {doc_id} -> {resp.status_code}")
    except Exception as e:
        print(f"Search sync error: {e}")

def _search_subs(user_id, query):
    """Ищет подписки через Vertex AI Search."""
    try:
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
        token = creds.token

        url = f"https://discoveryengine.googleapis.com/v1/{SEARCH_ENGINE}/servingConfigs/default_search:search"
        resp = httpx.post(url, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-goog-user-project": PROJECT_ID
        }, json={
            "query": query,
            "pageSize": 10
        }, timeout=10)
        if resp.status_code != 200:
            print(f"Search error: {resp.status_code} {resp.text[:200]}")
            return []
        results = []
        for r in resp.json().get("results", []):
            sd = r.get("document", {}).get("structData", {})
            if sd:
                results.append(sd)
        return results
    except Exception as e:
        print(f"Search error: {e}")
        return []

def _handle_ai_v2(chat_id, text):
    """V2: гибрид — действия через v1, разговор/анализ через Vertex AI Search."""
    # Сначала пробуем через обычный AI — он определит action
    subs = _get_subscriptions(chat_id)
    savings = _get_savings(chat_id)
    history = _append_history(chat_id, "user", text)
    
    try:
        result = _ask_ai(text, subs, savings, history)
    except Exception as e:
        print(f"V2 AI error: {e}")
        _append_history(chat_id, "bot", "⏳ AI временно недоступен.")
        _send_message(chat_id, "⏳ AI временно недоступен.")
        return

    action = result.get("action", "chat")

    # Действия (add/delete/list/savings_*) — через старую логику
    if action in ("add", "delete", "list", "savings_add", "savings_deposit", "savings_list", "savings_delete"):
        act, reply = _handle_ai_result(result, chat_id)
        # Синхронизируем в Search при добавлении
        if action == "add":
            _sync_sub_to_search(chat_id, {"name": result.get("name",""), "cost": result.get("cost",0), "currency": result.get("currency","$"), "renewal_date": result.get("renewal_date","")})
        reply = f"[v2] {reply}"
        _append_history(chat_id, "bot", reply)
        _send_message(chat_id, reply)
        return

    # Разговор/советы/анализ — через Vertex AI Search
    search_results = _search_subs(chat_id, text)
    if search_results:
        search_context = "\n".join([f"- {s.get('name','')} ({s.get('cost',0)} {s.get('currency','$')}) продление {s.get('renewal_date','')}" for s in search_results])
        grounded_prompt = f"""Ты — умный ассистент Ася-бот. Сегодня: {time.strftime("%Y-%m-%d, %A")}.

Данные пользователя (найдены через поиск):
{search_context}

Накопления:
{_format_savings(savings)}

Ответь на вопрос пользователя на основе этих данных. Будь дружелюбным и кратким. Отвечай на русском."""

        api_key = _get_secret("gemini-api-key")
        contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["text"]}]} for m in history]
        body = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": grounded_prompt}]},
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024},
        }
        for model in MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            try:
                resp = httpx.post(url, json=body, timeout=25)
                if resp.status_code in (429, 503):
                    continue
                resp.raise_for_status()
                raw = _extract_text(resp.json())
                if raw:
                    reply = f"[v2] {raw.strip()}"
                    _append_history(chat_id, "bot", reply)
                    _send_message(chat_id, reply)
                    return
            except Exception as e:
                print(f"V2 grounded {model}: {e}")
                continue

    # Fallback — обычный ответ
    reply = f"[v2] {result.get('reply', 'Не понял, попробуй ещё раз.')}"
    _append_history(chat_id, "bot", reply)
    _send_message(chat_id, reply)

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
            _send_message(chat_id, f"Привет! Я Ася-бот 🤖\nТвой ID: {chat_id}\n\nПиши мне как обычно:\n• «добавь Netflix 5 долларов, продление 1 мая»\n• «добавь Spotify 149 крон, продление 15 мая»\n• «какие у меня подписки?»\n• «что посоветуешь удалить?»\n\nИли команды: /list /add /delete /web /switch")
        elif text.startswith("/switch"):
            parts = text.split()
            mode = parts[1] if len(parts) > 1 else None
            if mode in ("v1", "v2"):
                _set_user_mode(chat_id, mode)
                if mode == "v2":
                    _send_message(chat_id, "🔬 Переключено на v2 (Vertex AI Search). Ответы будут с пометкой [v2].\nДля возврата: /switch v1")
                else:
                    _send_message(chat_id, "✅ Переключено на v1 (стандартный режим).\nДля v2: /switch v2")
            else:
                current = _get_user_mode(chat_id)
                _send_message(chat_id, f"Текущий режим: {current}\n\n/switch v1 — стандартный\n/switch v2 — Vertex AI Search (тест)")
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
            mode = _get_user_mode(chat_id)
            if mode == "v2":
                _handle_ai_v2(chat_id, text)
            else:
                _handle_ai_tg(chat_id, text)

        return "OK", 200
    except Exception as e:
        print(f"ERROR in webhook: {e}")
        return "OK", 200
