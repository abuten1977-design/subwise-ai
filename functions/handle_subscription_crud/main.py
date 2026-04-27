import functions_framework
from google.cloud import firestore
import json

db = firestore.Client()

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

def _user_subs(user_id):
    return db.collection("users").document(str(user_id)).collection("subscriptions")

@functions_framework.http
def handle_subscription_crud(request):
    if request.method == "OPTIONS":
        return "", 204, CORS_HEADERS

    try:
        # user_id from query param or JSON body
        user_id = request.args.get("user_id", "")
        data = request.get_json(silent=True) or {}
        if not user_id:
            user_id = data.get("user_id", "")
        if not user_id:
            return json.dumps({"error": "user_id required"}), 400, {**CORS_HEADERS, "Content-Type": "application/json"}

        col = _user_subs(user_id)

        if request.method == "POST":
            if not all(k in data for k in ("name", "cost", "renewal_date")):
                return "Missing fields: name, cost, renewal_date", 400, CORS_HEADERS
            name = data["name"]
            col.document(name).set({
                "name": name,
                "cost": float(data["cost"]),
                "currency": data.get("currency", "$"),
                "renewal_date": data["renewal_date"],
                "created_at": firestore.SERVER_TIMESTAMP,
            })
            return json.dumps({"status": "created", "name": name}), 201, {**CORS_HEADERS, "Content-Type": "application/json"}

        elif request.method == "GET":
            docs = col.stream()
            subs = [doc.to_dict() for doc in docs]
            return json.dumps(subs, default=str), 200, {**CORS_HEADERS, "Content-Type": "application/json"}

        elif request.method == "DELETE":
            name = data.get("name")
            if not name:
                return "Missing subscription name", 400, CORS_HEADERS
            col.document(name).delete()
            return json.dumps({"status": "deleted", "name": name}), 200, {**CORS_HEADERS, "Content-Type": "application/json"}

        return "Method Not Allowed", 405, CORS_HEADERS
    except Exception as e:
        return f"Error: {e}", 500, CORS_HEADERS
