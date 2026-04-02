import requests
import json

def send_staff_reminder(message_text):
    """
    Sends a push notification to all teachers via OneSignal.
    """
    # Replace these with your actual keys from the OneSignal Dashboard
    ONESIGNAL_APP_ID = "YOUR_APP_ID_HERE"
    ONESIGNAL_REST_API_KEY = "YOUR_REST_API_KEY_HERE"

    header = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Basic {ONESIGNAL_REST_API_KEY}"
    }

    payload = {
        "app_id": ONESIGNAL_APP_ID,
        "included_segments": ["All"], # Sends to everyone who has the app
        "headings": {"en": "Naura Staff Reminder"},
        "contents": {"en": message_text},
        "android_accent_color": "198754", # Your Naura Success Green
        "priority": 10
    }

    response = requests.post(
        "https://onesignal.com/api/v1/notifications",
        headers=header,
        data=json.dumps(payload)
    )
    
    return response.status_code