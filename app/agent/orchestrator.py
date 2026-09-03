import re
from typing import Any, Dict

from app.agent.prompt import build_prompt
from app.services.memory import MemoryService
from app.tools.approvals import ApprovalService
from app.tools.email_drafts import draft_proposal_email
from app.tools.leads import add_lead
from app.tools.tasks import add_task
from app.tools.team import assign_task_to_member


class AgentOrchestrator:
    def __init__(self) -> None:
        self.prompt = build_prompt()
        self.memory = MemoryService()
        self.approvals = ApprovalService()

    def handle_message(self, message: str) -> Dict[str, Any]:
        lowered = message.lower().strip()

        if lowered.startswith("add task"):
            match = re.match(r"add task:\s*(.+)", message, flags=re.IGNORECASE)
            title = match.group(1).strip() if match else message.replace("Add task:", "", 1).strip()
            task = add_task(title)
            return {"ok": True, "message": f"টাস্ক তৈরি হয়েছে: {task['title']}"}

        if lowered.startswith("add lead"):
            match = re.match(r"add lead:\s*(.+)", message, flags=re.IGNORECASE)
            details = match.group(1).strip() if match else message.replace("Add lead:", "", 1).strip()
            lead = add_lead(details)
            return {"ok": True, "message": f"লিড যোগ করা হয়েছে: {lead['name']}"}

        if lowered.startswith("assign") and "to" in lowered:
            match = re.match(r"assign\s+(.+?)\s+to\s+(.+)", message, flags=re.IGNORECASE)
            if match:
                task_title = match.group(1).strip()
                member_name = match.group(2).strip()
                return assign_task_to_member(task_title, member_name)

        if lowered.startswith("remember"):
            match = re.match(r"remember\s+that\s+my\s+(.+?)\s+is\s+(.+)", message, flags=re.IGNORECASE)
            if not match:
                match = re.match(r"remember\s+that\s+(.+?)\s+is\s+(.+)", message, flags=re.IGNORECASE)
            if match:
                key = match.group(1).strip()
                value = match.group(2).strip()
                self.memory.remember(key, value)
                return {"ok": True, "message": f"মনে রাখা হয়েছে: {key} = {value}"}

        if lowered.startswith("what do you know"):
            memory_items = self.memory.search("")
            if memory_items:
                details = "; ".join(f"{item['key']}: {item['value']}" for item in memory_items)
                return {"ok": True, "message": f"আমি জানি: {details}"}
            return {"ok": True, "message": "আমি এখনও তেমন কিছু জানি না।"}

        if lowered.startswith("draft") and "email" in lowered:
            client_name = message.replace("Draft a proposal email for", "", 1).strip()
            draft = draft_proposal_email(client_name)
            approval = self.approvals.create_approval("send email draft", client_name)
            return {
                "ok": True,
                "message": f"পাঠানোর আগে অনুমোদন প্রয়োজন। {client_name}-এর জন্য খসড়া তৈরি হয়েছে। {approval['message']}",
                "draft": draft,
            }

        if lowered.startswith("approve"):
            match = re.match(r"approve\s+(\d+)", message, flags=re.IGNORECASE)
            if match:
                return self.approvals.approve(int(match.group(1)))

        if lowered in {"hello", "hi", "hey"}:
            return {"ok": True, "message": "হ্যালো! আমি ট্রাই বাডি ওএস 🚀"}

        return {"ok": True, "message": f"আমি পেয়েছি: {message}"}
