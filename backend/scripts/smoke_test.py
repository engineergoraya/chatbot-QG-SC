"""
smoke_test.py — manual acceptance run of the 7 minimum questions from the
project spec, plus a handful from the 100-case bank. Prints question, SQL
used, confidence, and the answer for eyeball verification.

Run with the server already up:
    uvicorn app.main:app --port 8000
    python scripts/smoke_test.py
"""

import json
import urllib.request

BASE = "http://127.0.0.1:8000"

REQUIRED_QUESTIONS = [
    "What is current available inventory value?",
    "How many items are on water?",
    "Which suppliers are delayed?",
    "What did Production consume?",
    "Which items are critical?",
    "Which items need reorder?",
    "What is the stock of item 19981-60?",
]


def ask(question: str, session_id: str | None = None) -> dict:
    body = json.dumps({"question": question, "session_id": session_id}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def run(questions: list[str]):
    for i, q in enumerate(questions, 1):
        print(f"\n{'=' * 90}\n[{i}] Q: {q}")
        try:
            res = ask(q)
        except Exception as e:
            print(f"    ERROR calling API: {e}")
            continue
        print(f"    confidence: {res.get('confidence')}  needs_clarification: {res.get('needs_clarification')}")
        if res.get("sql_used"):
            print(f"    SQL: {res['sql_used']}")
        print(f"    A: {res.get('answer')}")


if __name__ == "__main__":
    run(REQUIRED_QUESTIONS)
