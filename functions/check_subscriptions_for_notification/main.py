import functions_framework
from google.cloud import firestore
from google.cloud import secretmanager
import os
import httpx
from datetime import datetime, timedelta

db = firestore.Client()
secret_client = secretmanager.SecretManagerServiceClient()

def _get_secret(secret_id, version_id="latest"):
    project_id = os.environ.get('GCP_PROJECT', 'autofriend-app-1773430739')
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    response = secret_client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8").strip()

def _send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = httpx.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    resp.raise_for_status()

@functions_framework.cloud_event
def check_subscriptions_for_notification(cloud_event):
    try:
        token = _get_secret("asya-app-tg-bot-token")
        today = datetime.utcnow().date()
        target = (today + timedelta(days=7)).isoformat()

        # Iterate all users
        for user_doc in db.collection("users").stream():
            user_id = user_doc.id
            subs = db.collection("users").document(user_id).collection("subscriptions").where("renewal_date", "==", target).stream()
            for doc in subs:
                s = doc.to_dict()
                cur = s.get("currency", "$")
                msg = (
                    f"🔔 Напоминание!\n\n"
                    f"<b>{s['name']}</b>\n"
                    f"Стоимость: {s['cost']:.2f} {cur}\n"
                    f"Дата оплаты: {s['renewal_date']}"
                )
                _send_message(token, user_id, msg)

    except Exception as e:
        print(f"ERROR in notification check: {e}")
