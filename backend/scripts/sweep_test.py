"""Ad-hoc broader sweep across departments to hunt for calculation bugs."""

import json
import urllib.request

BASE = "http://127.0.0.1:8000"

QUESTIONS = [
    ("Purchase", "What is our total purchase value?"),
    ("Purchase", "Which supplier has the best on-time delivery?"),
    ("Purchase", "How much did we buy on credit vs cash?"),
    ("Purchase", "Who are our top 10 suppliers by spend?"),
    ("Imports", "What value of imports is currently at sea?"),
    ("Imports", "How dependent are we on China for imports?"),
    ("Imports", "How many shipments are ready awaiting sailing?"),
    ("Logistics", "What is our average transit time?"),
    ("Logistics", "What is our freight cost per kg?"),
    ("Logistics", "How many shipments have complete documentation?"),
    ("Issuance", "What is our total consumption value?"),
    ("Issuance", "Which department consumed the most?"),
    ("Issuance", "How much did job SE25-LAGE-0008 consume?"),
    ("Cross-functional", "Are we buying what we consume?"),
]


def ask(question, sid=None):
    body = json.dumps({"question": question, "session_id": sid}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())


for dept, q in QUESTIONS:
    print(f"\n{'=' * 90}\n[{dept}] Q: {q}")
    try:
        res = ask(q)
    except Exception as e:
        print(f"    ERROR: {e}")
        continue
    print(f"    confidence: {res.get('confidence')}  needs_clarification: {res.get('needs_clarification')}")
    if res.get("sql_used"):
        print(f"    SQL: {res['sql_used']}")
    print(f"    A: {res.get('answer')}")
