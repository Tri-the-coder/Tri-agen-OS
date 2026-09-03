from typing import Dict, Any


def draft_proposal_email(client_name: str, project_value: str = "") -> Dict[str, Any]:
    subject = f"Proposal for {client_name}"
    body = (
        f"Hi {client_name},\n\n"
        "Thank you for considering our services. "
        f"We would be happy to support your project{(' for ' + project_value) if project_value else ''}.\n\n"
        "Best regards,\nTri"
    )
    return {"ok": True, "subject": subject, "body": body, "message": "পাঠানোর আগে অনুমোদন প্রয়োজন।"}
