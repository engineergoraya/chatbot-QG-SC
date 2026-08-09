# Maintenance notes

Operational notes for keeping the chatbot's answers correct as the
underlying data changes. This file is documentation only and is not
imported or read by any application code.

## After a data reload

1. Re-run `backend/scripts/setup_readonly_role.sql` if `chatbot_ro` loses
   SELECT access — a full table drop/recreate resets grants even though
   `ALTER DEFAULT PRIVILEGES` is already in place for future reloads.
2. Run `backend/tests/test_prompt_figures_current.py` to check whether any
   VERIFIED figure quoted in `business_rules.py` has drifted from the live
   data (it skips cleanly if no database is reachable).
3. Spot-check a few questions across each domain (purchases, stock,
   issuance, imports, logistics) end-to-end before relying on the answers.
