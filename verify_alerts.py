import requests
import time
import json
from datetime import datetime, timedelta

# Configuration
BASE_URL = "http://localhost:8000"
WEBHOOK_URL = f"{BASE_URL}/webhook/alert"

def test_webhook():
    print("Testing Webhook...")
    
    # Payload simulating Alertmanager
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighLatency",
                    "namespace": "default",
                    "service": "frontend",
                    "severity": "critical"
                },
                "annotations": {
                    "description": "Latency is high",
                    "summary": "High latency detected"
                },
                "startsAt": datetime.utcnow().isoformat() + "Z",
                "fingerprint": "test-fingerprint-1"
            },
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighErrorRate",
                    "namespace": "default",
                    "service": "frontend",
                    "severity": "high"
                },
                "annotations": {
                    "description": "Error rate > 5%",
                    "summary": "High error rate"
                },
                "startsAt": datetime.utcnow().isoformat() + "Z",
                "fingerprint": "test-fingerprint-2"
            }
        ]
    }
    
    try:
        response = requests.post(WEBHOOK_URL, json=payload)
        print(f"Webhook Response: {response.status_code} {response.text}")
        if response.status_code == 202:
            print("✅ Webhook accepted alerts")
        else:
            print("❌ Webhook failed")
            return
    except Exception as e:
        print(f"❌ Webhook request failed: {e}")
        return

    # Run Batcher Manually (since service is not running)
    print("Running Batcher manually...")
    from project_fyr.service import AlertBatcher
    from project_fyr.db import init_db, AlertRepo
    from project_fyr.config import settings
    
    engine = init_db(settings.database_url)
    repo = AlertRepo(engine)
    batcher = AlertBatcher(repo, settings)
    batcher.run_once()
    
    # Check Dashboard API (simulated via HTML check or direct DB check if we had access)
    # We'll check the /alerts page content
    try:
        response = requests.get(f"{BASE_URL}/alerts")
        if response.status_code == 200:
            if "default / frontend" in response.text:
                print("✅ Alert batch visible in dashboard")
            else:
                print("❌ Alert batch NOT found in dashboard")
                print(response.text[:500])
        else:
            print(f"❌ Failed to fetch alerts page: {response.status_code}")
    except Exception as e:
        print(f"❌ Dashboard check failed: {e}")

if __name__ == "__main__":
    test_webhook()
