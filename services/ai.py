from typing import Any, Dict, Optional

from services.memory import ConversationMemory
from zenzap_client import ZenzapClient
from openrouter_client import OpenRouterClient


class AIService:
    def __init__(self, ai_client: OpenRouterClient, memory: ConversationMemory, zenzap_client: ZenzapClient) -> None:
        self.ai_client = ai_client
        self.memory = memory
        self.zenzap_client = zenzap_client

    def generate_reply(self, message: str, session_id: str = "default") -> Dict[str, Any]:
        self.memory.add_message(session_id, "user", message)
        history = self.memory.get_history(session_id)
        prompt = self._build_prompt(history)
        response = self.ai_client.generate_response(prompt)
        if response.get("ok"):
            self.memory.add_message(session_id, "assistant", response.get("response", ""))
        return response

    def send_to_zenzap(self, message: str) -> Dict[str, Any]:
        return self.zenzap_client.send_message(message)

    def _build_prompt(self, history: list[dict]) -> str:
        if not history:
            return "You are a helpful assistant."

        return "\n".join(
            f"{item['role']}: {item['content']}" for item in history[-8:]
        )
