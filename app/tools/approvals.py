from typing import Any, Dict


class ApprovalService:
    def __init__(self) -> None:
        self.pending: list[Dict[str, Any]] = []

    def create_approval(self, action: str, details: str) -> Dict[str, Any]:
        approval_id = len(self.pending) + 1
        self.pending.append({"id": approval_id, "action": action, "details": details})
        return {
            "ok": True,
            "message": f"{action}-এর জন্য অনুমোদন প্রয়োজন। নিশ্চিত করতে 'APPROVE {approval_id}' লিখে পাঠান।",
            "approval_id": approval_id,
        }

    def approve(self, approval_id: int) -> Dict[str, Any]:
        for item in self.pending:
            if item["id"] == approval_id:
                self.pending.remove(item)
                return {"ok": True, "message": f"অনুমোদিত হয়েছে: {item['action']}"}
        return {"ok": False, "message": "অনুমোদন খুঁজে পাওয়া যায়নি"}
