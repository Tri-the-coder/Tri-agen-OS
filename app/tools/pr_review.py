import logging
from typing import Any, Dict, Optional

from app.services.models import AllModelsUnavailable, complete

logger = logging.getLogger(__name__)

# Free models have large contexts, but a giant diff costs latency and dilutes
# attention. Reviews past this point are truncated and the report says so.
MAX_DIFF_CHARS = 60_000

REVIEW_PROMPT = """You are reviewing one pull request for the team building Babosayee.

WHAT YOU CAN SEE
Only the diff below. You cannot see the rest of the codebase, the tests, the issue it
closes, or whether CI passed. You cannot run anything.

HARD RULES
- Comment only on lines present in the diff. Never invent a file name, a line number, a
  function, or code you were not shown.
- Never say tests pass or fail, never say the build is green, never claim you ran anything.
- If a change looks wrong only in a way you cannot confirm without seeing other files, say
  that explicitly rather than asserting a bug: "if <X> is not handled in <caller>, this
  would ...".
- If the diff is truncated, do not guess at the rest.
- An empty finding list is a valid and useful result. Do not manufacture issues to look
  thorough.

WHAT TO LOOK FOR, in priority order
1. Correctness bugs: wrong logic, off-by-one, unhandled None/empty, swapped arguments,
   broken error handling, race conditions.
2. Security: injected SQL or shell, secrets committed, missing authz checks, unsafe
   deserialisation, unbounded external calls with no timeout.
3. Data loss: destructive migrations, DROP, overwrites, missing transactions.
4. Then, briefly: duplication, dead code, naming that misleads.
Ignore pure formatting.

OUTPUT FORMAT
Start with one line: a verdict of LGTM, COMMENTS, or NEEDS WORK, then a short reason.
Then, only if you have findings, a list. Each one:
- *<file>* — what is wrong, and what to do instead. One or two sentences.
Finish with one line on what a reviewer should check manually that you could not.
Keep the whole thing under 250 words. Be specific, not encouraging."""


def build_context(pr: Dict[str, Any]) -> str:
    diff = pr.get("diff", "") or ""
    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS]

    header = [
        f"PR #{pr.get('number')}: {pr.get('title')}",
        f"Author: {pr.get('author')}",
        f"Files changed: {pr.get('files_changed')} (+{pr.get('additions')}/-{pr.get('deletions')})",
    ]
    if truncated:
        header.append(
            "NOTE: the diff below is truncated. Review only what is shown and say so."
        )
    return "\n".join(header) + "\n\n```diff\n" + diff + "\n```"


def review(pr: Dict[str, Any]) -> Dict[str, Any]:
    """Run one review. Never raises: a failed review must not fail the PR check."""
    if not (pr.get("diff") or "").strip():
        return {"ok": False, "error": "empty diff", "text": None}

    try:
        result = complete(REVIEW_PROMPT, build_context(pr))
    except AllModelsUnavailable as error:
        logger.warning("PR review skipped, models unavailable | error=%s", error)
        return {"ok": False, "error": "all free models rate-limited", "text": None,
                "rate_limited": error.rate_limited}
    except Exception as error:  # noqa: BLE001
        logger.exception("PR review failed | error=%s", error)
        return {"ok": False, "error": str(error)[:200], "text": None}

    return {"ok": True, "model": result["model"], "text": result["content"].strip()}


def format_for_slack(pr: Dict[str, Any], result: Dict[str, Any]) -> str:
    title = pr.get("title") or "(no title)"
    number = pr.get("number")
    url = pr.get("url") or ""
    author = pr.get("author") or "unknown"
    head = f"*<{url}|PR #{number}: {title}>* by {author}" if url else f"*PR #{number}: {title}* by {author}"
    stats = (
        f"{pr.get('files_changed', '?')} files "
        f"(+{pr.get('additions', '?')}/-{pr.get('deletions', '?')})"
    )

    if not result.get("ok"):
        return (
            f"{head}\n{stats}\n\n"
            f"Automated review did not run: `{result.get('error')}`. "
            "Nothing was checked, so this PR has had no review."
        )

    return (
        f"{head}\n{stats}\n\n{result['text']}\n\n"
        f"_First-pass review by {result['model']}, from the diff only. "
        "It cannot see the rest of the codebase, the tests, or CI, and is not a substitute "
        "for a human reviewer._"
    )
