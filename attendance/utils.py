import json
import os

import requests

def send_staff_reminder(message_text):
    """
    Sends a push notification to all teachers via OneSignal.
    """
    onesignal_app_id = os.getenv("ONESIGNAL_APP_ID", "YOUR_APP_ID_HERE")
    onesignal_rest_api_key = os.getenv("ONESIGNAL_REST_API_KEY", "YOUR_REST_API_KEY_HERE")

    if onesignal_app_id == "YOUR_APP_ID_HERE" or onesignal_rest_api_key == "YOUR_REST_API_KEY_HERE":
        return {
            "ok": False,
            "status_code": None,
            "error": "OneSignal credentials are not configured.",
        }

    header = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Basic {onesignal_rest_api_key}"
    }

    payload = {
        "app_id": onesignal_app_id,
        "included_segments": ["All"], # Sends to everyone who has the app
        "headings": {"en": "School Staff Reminder"},
        "contents": {"en": message_text},
        "android_accent_color": "198754", # Default portal accent
        "priority": 10
    }

    try:
        response = requests.post(
            "https://onesignal.com/api/v1/notifications",
            headers=header,
            data=json.dumps(payload),
            timeout=15,
        )
        return {
            "ok": response.ok,
            "status_code": response.status_code,
            "error": "" if response.ok else response.text[:300],
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status_code": None,
            "error": str(exc),
        }
