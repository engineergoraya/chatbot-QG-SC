# Changelog

Notable changes to the Qadri Group AI Supply Chain Assistant, newest first.
This file is documentation only and is not read by the application at
runtime.

## 2026-08

- Fixed two classes of answer-stage narration errors (row count read as a
  business quantity; PO lines read as orders) by moving grain/counting rules
  into the answer-explanation prompt, not just SQL generation.
- Restored `chatbot_ro` read access after a data reload dropped table grants;
  `ALTER DEFAULT PRIVILEGES` now keeps future reloads working automatically.
- Refreshed the VERIFIED figures in `business_rules.py` against the current
  data load and added `test_prompt_figures_current.py` to catch future drift.
- Added guard-level checks for unknown SQL columns, a fabricated export-delay
  pattern, and an import value total missing its outer `SUM`.
