-- ============================================================================
-- fix_verified_functions.sql
--
-- Corrected definitions for the three read-only helper functions the chatbot
-- calls (current_stock_of, supplier_delay, reorder_recommendation).
--
-- REVIEW BEFORE RUNNING. Run in pgAdmin as the OWNER role (not chatbot_ro).
-- Nothing here writes data: every function is LANGUAGE sql / STABLE /
-- SECURITY INVOKER and only ever SELECTs.
--
-- ----------------------------------------------------------------------------
-- WHY: two bugs were confirmed against the live database on 2026-07-31.
--
-- BUG 1 — matching only ever looked at items.item.
--   All three functions used:  WHERE i.item ILIKE '%' || search_item || '%'
--   Grade/code tokens are stored in items.specs, NOT items.item — e.g.
--   item_code 16425-60 is item='Resin', specs='A-85'. So:
--     * reorder_recommendation('a85')  -> 0 rows
--     * current_stock_of('a85')        -> 0 rows
--     * 'resin a85' (as one phrase)    -> 0 rows, since those two words never
--       appear contiguously in any single column.
--   FIX: match against a punctuation-stripped blob of item + specs +
--   group_name + material_standard + item_category, tokenised so that plain
--   words are AND-ed and grade-like tokens (containing a digit) are OR-ed
--   among themselves — 'resin a85 1085' means resin AND (a85 OR 1085),
--   because each grade is usually a DIFFERENT item_code.
--
-- BUG 2 — reorder_recommendation inner-joined stock.
--   It used:  FROM items i JOIN stock_agg st ON st.item_code = i.item_code
--   `stock` is a PARTIAL snapshot: 8,274 of the 11,956 items that have
--   issuance history (69%) have no stock row at all. The inner join silently
--   deleted all of them, and the chatbot reported that as "the item may not
--   exist in the data".
--   Confirmed case: 26287-60 Resin / 'A-85 / 103 / 1085' is rank 'A', has
--   ab_items rows at both branches and is consumed at ~1,391 kg/day at
--   Qadcast — but has no stock row, so it was invisible and the bot answered
--   about the minor 'Resin Sand' item instead.
--   FIX: anchor on the matched items and LEFT JOIN stock / ab_items / usage
--   onto a branch "spine" (the UNION of branches seen in stock and issuance),
--   so such items still return a row with NULL available_qty and an explicit
--   "no stock row" recommendation instead of vanishing.
--
-- ----------------------------------------------------------------------------
-- IMPORTANT — DROP is required, not just CREATE OR REPLACE.
--   All three functions gain columns (specs/uom/branch), and CREATE OR REPLACE
--   cannot change a function's return type. The DROPs below are therefore
--   mandatory. Dropping also discards existing GRANTs, which is why the
--   GRANT EXECUTE ... TO chatbot_ro statements at the bottom must be run too.
--
-- Also note: reorder_recommendation now returns ONE ROW PER ITEM+BRANCH
-- instead of one aggregated row per item. The previous version SUM-ed stock
-- and MAX-ed lead_time across branches, which contradicts the per-branch rule
-- in app/knowledge/business_rules.py (rule 4) — branches have different
-- lead_time_days (e.g. 26287-60 is 90 days at Qadcast, 45 at Unit-II), so a
-- single blended figure was not meaningful.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 0. Shared matching helpers (new)
--    Centralising the fix for BUG 1 so all three functions stay consistent.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.item_search_norm(txt text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SECURITY INVOKER
AS $function$
  -- For a SEARCH TOKEN: lowercase + strip every non-alphanumeric character,
  -- so a user typing "a85" lines up with a stored "A-85", and "%"/"_" in user
  -- input cannot act as LIKE wildcards or regex metacharacters.
  SELECT regexp_replace(lower(coalesce(txt, '')), '[^a-z0-9]', '', 'g');
$function$;


CREATE OR REPLACE FUNCTION public.item_search_haystack(txt text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SECURITY INVOKER
AS $function$
  -- For the SEARCHED TEXT: strip punctuation WITHIN each word but KEEP the
  -- word boundaries, then collapse whitespace. 'Resin A-85 / 103 / 1085'
  -- becomes 'resin a85 103 1085'.
  --
  -- Keeping the spaces matters. If every space were stripped too, a grade
  -- token would match across word boundaries and produce false positives —
  -- confirmed against live data: searching 'a85' then also matched
  -- 'Drill Solid / 308FA-8.5-80-A10', 'Gas Compressor / KADA 850G' and
  -- 'Digital Soldering Rework Station / KADA 852d+', none of which are A-85
  -- items. With boundaries preserved, grade matching can be anchored to the
  -- start of a word (see items_matching) and those drop out.
  SELECT btrim(
           regexp_replace(
             regexp_replace(lower(coalesce(txt, '')), '[^a-z0-9 ]', '', 'g'),
             ' +', ' ', 'g'
           )
         );
$function$;


CREATE OR REPLACE FUNCTION public.items_matching(search_item text)
RETURNS TABLE(item_code text, item_name text, specs text, uom text)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $function$
  WITH tokens AS (
    SELECT public.item_search_norm(tok) AS tok,
           (tok ~ '[0-9]')              AS is_grade
    FROM regexp_split_to_table(lower(coalesce(search_item, '')), '\s+') AS tok
    WHERE tok <> ''
  ),
  blob AS (
    SELECT i.item_code, i.item, i.specs, i.uom,
           public.item_search_haystack(
             coalesce(i.item, '')              || ' ' ||
             coalesce(i.specs, '')             || ' ' ||
             coalesce(i.group_name, '')        || ' ' ||
             coalesce(i.material_standard, '') || ' ' ||
             coalesce(i.item_category, '')
           ) AS haystack
    FROM items i
  )
  SELECT b.item_code, b.item, b.specs, b.uom
  FROM blob b
  WHERE
    -- an empty/blank search term must match NOTHING, not everything
    EXISTS (SELECT 1 FROM tokens t WHERE t.tok <> '')
    -- every plain word must appear somewhere in the blob (AND).
    -- Substring match, so 'resin' still finds 'Phenolic Resin'.
    AND NOT EXISTS (
      SELECT 1 FROM tokens t
      WHERE NOT t.is_grade
        AND t.tok <> ''
        AND b.haystack NOT LIKE '%' || t.tok || '%'
    )
    -- if any grade-like tokens were given, at least ONE must appear (OR) —
    -- 'resin a85 1085' means resin AND (a85 OR 1085), because each grade is
    -- usually a different item_code and no single row carries them all.
    -- Anchored to the start of a word to avoid the cross-boundary false
    -- positives described in item_search_haystack.
    AND (
      NOT EXISTS (SELECT 1 FROM tokens t WHERE t.is_grade AND t.tok <> '')
      OR EXISTS (
        SELECT 1 FROM tokens t
        WHERE t.is_grade
          AND t.tok <> ''
          AND b.haystack ~ ('(^| )' || t.tok)
      )
    );
$function$;


-- ----------------------------------------------------------------------------
-- 1. current_stock_of
--
-- BEFORE:
--   RETURNS TABLE(item_code text, item_name text, qty numeric)
--     SELECT s.item_code, i.item, s.available_qty
--     FROM stock s
--     JOIN items i ON i.item_code = s.item_code
--     WHERE i.item ILIKE '%' || search_item || '%';
--
-- AFTER (changes):
--   * matches item + specs + group/material/category, punctuation-stripped
--     (BUG 1) — 'a85' and 'resin a85' now resolve.
--   * anchors on the matched items and LEFT JOINs stock (BUG 2), so an item
--     with no stock row returns available_qty NULL rather than disappearing.
--   * adds specs (tells grades apart), uom (a bare quantity is unusable —
--     business rule 16) and branch (stock is per item+branch; the old
--     unqualified "qty" hid which branch it belonged to).
-- ----------------------------------------------------------------------------

DROP FUNCTION IF EXISTS public.current_stock_of(text);

CREATE OR REPLACE FUNCTION public.current_stock_of(search_item text)
RETURNS TABLE(
  item_code     text,
  item_name     text,
  specs         text,
  uom           text,
  branch        text,
  available_qty numeric
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $function$
  SELECT m.item_code, m.item_name, m.specs, m.uom, s.branch, s.available_qty
  FROM public.items_matching(search_item) m
  LEFT JOIN stock s ON s.item_code = m.item_code
  ORDER BY m.item_code, s.branch NULLS LAST;
$function$;


-- ----------------------------------------------------------------------------
-- 2. supplier_delay
--
-- BEFORE:
--   RETURNS TABLE(item_code text, supplier text, required_d date,
--                 purchase date, delay_days integer, qty integer,
--                 amount numeric)
--     ... WHERE p.required_d IS NOT NULL AND p.purchase IS NOT NULL
--         AND (search_item IS NULL OR p.item_code IN (
--               SELECT i.item_code FROM items i
--               WHERE i.item ILIKE '%' || search_item || '%'))
--     ORDER BY (p.purchase - p.required_d) DESC;
--
-- AFTER (changes):
--   * item resolution goes through items_matching (BUG 1).
--   * adds item_name + specs, LEFT JOINed for enrichment so a purchase row
--     whose item_code has no items row is still returned (business rule 22).
--   * BUG 2 deliberately does NOT apply here: this function answers "which
--     purchase orders were late", so an item with no purchase orders
--     genuinely has no rows — returning empty is correct, not a silent drop.
--   * keeps the DEFAULT NULL (NULL = all items) and the delay ordering.
-- ----------------------------------------------------------------------------

DROP FUNCTION IF EXISTS public.supplier_delay(text);

CREATE OR REPLACE FUNCTION public.supplier_delay(search_item text DEFAULT NULL)
RETURNS TABLE(
  item_code  text,
  item_name  text,
  specs      text,
  supplier   text,
  required_d date,
  purchase   date,
  delay_days integer,
  qty        integer,
  amount     numeric
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $function$
  SELECT p.item_code, i.item, i.specs, p.supplier, p.required_d, p.purchase,
         (p.purchase - p.required_d)::int AS delay_days,
         p.qty, p.amount
  FROM purchases_data p
  LEFT JOIN items i ON i.item_code = p.item_code
  WHERE p.required_d IS NOT NULL
    AND p.purchase   IS NOT NULL
    AND (
      search_item IS NULL
      OR p.item_code IN (SELECT m.item_code FROM public.items_matching(search_item) m)
    )
  ORDER BY (p.purchase - p.required_d) DESC;
$function$;


-- ----------------------------------------------------------------------------
-- 3. reorder_recommendation
--
-- BEFORE (abridged): items JOIN stock_agg (INNER) with stock SUM-ed and
--   lead_time/safety MAX-ed across all branches, matching items.item only:
--     FROM items i
--     JOIN stock_agg st ON st.item_code = i.item_code   <-- BUG 2
--     LEFT JOIN use_agg u ON u.item_code = i.item_code
--     LEFT JOIN lt        ON lt.item_code = i.item_code
--     WHERE i.item ILIKE '%' || search_item || '%'      <-- BUG 1
--
-- AFTER (changes):
--   * items_matching for resolution (BUG 1).
--   * anchored on matched items, LEFT JOIN stock/usage/ab_items onto a branch
--     spine (BUG 2) — items with no stock row now return a row explaining
--     that, instead of vanishing.
--   * one row per item+branch, using that branch's OWN lead_time/safety and
--     its OWN daily usage (business rule 4) rather than a cross-branch blend.
--   * daily usage is spread over the branch's calendar span
--     (SUM(qty) / (MAX(date) - MIN(date) + 1)), matching business rule 10's
--     "average daily rate" definition and the previous behaviour.
--   * daily_use / reorder_level stay NULL when unknown — never COALESCE-d to
--     0, which would turn "unknown" into a false "zero usage".
-- ----------------------------------------------------------------------------

DROP FUNCTION IF EXISTS public.reorder_recommendation(text);

CREATE OR REPLACE FUNCTION public.reorder_recommendation(search_item text)
RETURNS TABLE(
  item_code             text,
  item_name             text,
  specs                 text,
  uom                   text,
  branch                text,
  available_qty         numeric,
  daily_use             numeric,
  days_of_stock         numeric,
  lead_time_days        integer,
  safety_days           integer,
  reorder_level         numeric,
  days_until_reorder    numeric,
  projected_reorder_date date,
  recommendation        text
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $function$
  WITH mi AS (
    SELECT m.item_code, m.item_name, m.specs, m.uom
    FROM public.items_matching(search_item) m
  ),
  spine AS (   -- every item+branch that has EITHER stock OR issuance history
    SELECT s.item_code, s.branch FROM stock s
    WHERE s.item_code IN (SELECT m.item_code FROM mi m)
    UNION
    SELECT g.item_code, g.branch FROM issuance g
    WHERE g.item_code IN (SELECT m.item_code FROM mi m)
  ),
  usage AS (
    SELECT g.item_code, g.branch,
           SUM(g.quantity) / NULLIF(MAX(g.from_date) - MIN(g.from_date) + 1, 0) AS daily_use,
           COUNT(*)                                  AS issue_lines,
           MAX(g.from_date) - MIN(g.from_date)       AS span_days
    FROM issuance g
    WHERE g.item_code IN (SELECT m.item_code FROM mi m)
      AND g.from_date IS NOT NULL
    GROUP BY g.item_code, g.branch
  ),
  calc AS (
    SELECT m.item_code, m.item_name, m.specs, m.uom, sp.branch,
           s.available_qty,
           u.daily_use, u.issue_lines, u.span_days,
           ab.lead_time_days, ab.safety_days,
           (u.daily_use * (ab.lead_time_days + ab.safety_days)) AS reorder_level
    FROM mi m
    LEFT JOIN spine    sp ON sp.item_code = m.item_code
    LEFT JOIN stock    s  ON s.item_code  = m.item_code AND s.branch       = sp.branch
    LEFT JOIN usage    u  ON u.item_code  = m.item_code AND u.branch       = sp.branch
    LEFT JOIN ab_items ab ON ab.item_code = m.item_code AND ab.branch_name = sp.branch
  )
  SELECT
    c.item_code,
    c.item_name,
    c.specs,
    c.uom,
    c.branch,
    c.available_qty,
    ROUND(c.daily_use, 3),
    ROUND(c.available_qty / NULLIF(c.daily_use, 0), 1),
    c.lead_time_days,
    c.safety_days,
    ROUND(c.reorder_level, 2),
    ROUND((c.available_qty - c.reorder_level) / NULLIF(c.daily_use, 0), 1),
    (CURRENT_DATE
      + ((c.available_qty - c.reorder_level) / NULLIF(c.daily_use, 0)) * INTERVAL '1 day')::date,
    CASE
      WHEN c.available_qty IS NULL THEN
        'No stock row for this item/branch — not carried in the current stock '
        || 'snapshot, so no reorder date can be projected'
        || CASE WHEN c.daily_use IS NOT NULL
                THEN ' (but it IS being consumed, at about '
                     || ROUND(c.daily_use, 2)::text || ' ' || coalesce(c.uom, 'units')
                     || '/day at this branch)'
                ELSE '' END
      WHEN c.daily_use IS NULL OR c.issue_lines < 2 OR COALESCE(c.span_days, 0) < 30 THEN
        'Insufficient issuance history at this branch to recommend a reorder'
      WHEN c.lead_time_days IS NULL OR c.safety_days IS NULL THEN
        'No ab_items lead-time/safety data for this branch — buffer not applied; about '
        || ROUND(c.available_qty / NULLIF(c.daily_use, 0), 0)::text || ' days of stock remain'
      WHEN c.available_qty <= c.reorder_level THEN
        'REORDER NOW — at or below reorder level ('
        || ROUND(c.reorder_level, 0)::text || ' ' || coalesce(c.uom, 'units') || ')'
      ELSE
        'OK for now — reorder in about '
        || ROUND((c.available_qty - c.reorder_level) / NULLIF(c.daily_use, 0))::text || ' days'
    END
  FROM calc c
  ORDER BY c.item_code, c.branch NULLS LAST;
$function$;


-- ----------------------------------------------------------------------------
-- 4. GRANTs — required, because DROP FUNCTION discarded the previous ones.
--    The two helpers need EXECUTE as well: these are SECURITY INVOKER, so the
--    calling role (chatbot_ro) executes the inner functions as itself.
-- ----------------------------------------------------------------------------

GRANT EXECUTE ON FUNCTION public.item_search_norm(text)        TO chatbot_ro;
GRANT EXECUTE ON FUNCTION public.item_search_haystack(text)    TO chatbot_ro;
GRANT EXECUTE ON FUNCTION public.items_matching(text)          TO chatbot_ro;
GRANT EXECUTE ON FUNCTION public.current_stock_of(text)        TO chatbot_ro;
GRANT EXECUTE ON FUNCTION public.supplier_delay(text)          TO chatbot_ro;
GRANT EXECUTE ON FUNCTION public.reorder_recommendation(text)  TO chatbot_ro;


-- ============================================================================
-- 5. Verification — run these after applying, ideally connected as chatbot_ro.
--    Expected results were confirmed against live data on 2026-07-31.
-- ============================================================================
--
-- -- (a) The regression that started this. Was 0 rows; should now be 9 rows
-- --     across 16425-60 / 24284-60 / 24612-60 / 26287-60, each showing
-- --     available_qty NULL and a real daily_use.
-- SELECT * FROM reorder_recommendation('resin a85 1085');
--
-- -- (b) Grade-only search. Was 0 rows (A-85 lives in specs); should now match.
-- SELECT * FROM current_stock_of('a85');
--
-- -- (c) Unchanged behaviour check: 24370-60 Resin Sand at Unit-II should
-- --     still read available_qty 1034, daily_use ~3.016, reorder_level ~316,
-- --     projected_reorder_date 2027-03-26, "reorder in about 238 days".
-- SELECT * FROM reorder_recommendation('resin sand');
--
-- -- (d) The item that was previously invisible: 26287-60 should appear with
-- --     lead_time_days 90 (Qadcast) / 45 (Unit-II) and daily_use ~1391 / ~598.
-- SELECT * FROM reorder_recommendation('resin')
-- WHERE item_code = '26287-60';
--
-- -- (e) Blank search must match nothing rather than the whole catalogue.
-- SELECT count(*) AS should_be_zero FROM current_stock_of('   ');
--
-- -- (f) Grade matching must be word-anchored: 'a85' should return the six
-- --     genuine A-85 items (16425-60, 24284-60, 24348-60, 24355-60,
-- --     24434-60, 26287-60) and must NOT return 10618-60 (308FA-8.5-80-A10),
-- --     20344-60 (KADA 850G) or 23161-60 (KADA 852d+).
-- SELECT item_code, item_name, specs FROM items_matching('a85') ORDER BY item_code;
