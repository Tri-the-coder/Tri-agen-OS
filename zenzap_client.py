import os
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

load_dotenv()


class ZenzapClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("ZENZAP_API_KEY", "").strip()
        self.api_secret = os.getenv("ZENZAP_API_SECRET", "").strip()
        self.topic_id = os.getenv("ZENZAP_TOPIC_ID", "").strip()
        self.base_url = os.getenv("ZENZAP_BASE_URL", "https://api.zenzap.co").rstrip("/")

    def health_check(self) -> Dict[str, Any]:
        if not self.api_key or not self.api_secret:
            return {"ok": False, "error": "Missing Zenzap credentials"}

        endpoints = [
            f"{self.base_url}/health",
            f"{self.base_url}/",
        ]

        for endpoint in endpoints:
            try:
                response = requests.get(endpoint, timeout=15)
                if response.ok:
                    return {
                        "ok": True,
                        "endpoint": endpoint,
                        "status": response.status_code,
                        "body": response.text[:500],
                    }
            except requests.RequestException as exc:
                continue

        return {"ok": False, "error": "Unable to reach Zenzap health endpoint"}

    def send_message(self, message: str) -> Dict[str, Any]:
        if not self.topic_id:
            return {"ok": False, "error": "ZENZAP_TOPIC_ID is not configured"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "topic_id": self.topic_id,
            "message": message,
        }

        endpoints = [
            f"{self.base_url}/messages",
            f"{self.base_url}/v1/messages",
            f"{self.base_url}/topics/{self.topic_id}/messages",
            f"{self.base_url}/v1/topics/{self.topic_id}/messages",
        ]

        for endpoint in endpoints:
            try:
                response = requests.post(endpoint, headers=headers, json=payload, timeout=20)
                if response.ok:
                    try:
                        return {"ok": True, "endpoint": endpoint, "body": response.json()}
                    except ValueError:
                        return {"ok": True, "endpoint": endpoint, "body": response.text}
            except requests.RequestException:
                continue

        return {
            "ok": False,
            "error": "Unable to send message to Zenzap with the configured endpoint pattern",
            "attempted_endpoints": endpoints,
        }
