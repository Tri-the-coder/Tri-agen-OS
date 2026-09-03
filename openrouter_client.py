import os
from typing import Any, Dict

import requests
from dotenv import load_dotenv

load_dotenv()


class OpenRouterClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.model = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct").strip()
        self.base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

    def health_check(self) -> Dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "error": "OPENROUTER_API_KEY is not configured"}

        try:
            response = requests.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=20,
            )
            if response.ok:
                return {"ok": True, "status": response.status_code, "model": self.model}
            return {"ok": False, "error": response.text[:500], "status": response.status_code}
        except requests.RequestException as exc:
            return {"ok": False, "error": str(exc)}

    def generate_response(self, prompt: str, system_prompt: str = "You are a helpful assistant.") -> Dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "error": "OPENROUTER_API_KEY is not configured"}

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("APP_URL", "https://onrender.com"),
            "X-Title": "Telegram Control Bot",
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"ok": True, "model": self.model, "response": content, "raw": data}
        except requests.RequestException as exc:
            return {"ok": False, "error": str(exc)}
