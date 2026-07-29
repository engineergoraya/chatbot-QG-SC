#------------------- EXPORTS SHEET VIEW --------------------------------------
exports_view = '''CREATE OR REPLACE VIEW v_documentation_completion AS
SELECT
    e.export_id,
    e.exp_no,
    e.batch_no,
    COUNT(d.document_id)                                          AS total_documents,
    COUNT(*) FILTER (WHERE d.status = 'Done')                     AS completed_documents,
    COUNT(*) FILTER (WHERE d.status IS DISTINCT FROM 'Done')      AS pending_documents,
    ROUND(100.0 * COUNT(*) FILTER (WHERE d.status = 'Done')
        / NULLIF(COUNT(d.document_id), 0), 1)                     AS completion_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE d.party = 'Customs'  AND d.status = 'Done')
        / NULLIF(COUNT(*) FILTER (WHERE d.party = 'Customs'), 0), 1)  AS customs_completion_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE d.party = 'Customer' AND d.status = 'Done')
        / NULLIF(COUNT(*) FILTER (WHERE d.party = 'Customer'), 0), 1) AS customer_completion_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE d.party = 'Bank'     AND d.status = 'Done')
        / NULLIF(COUNT(*) FILTER (WHERE d.party = 'Bank'), 0), 1)     AS bank_completion_pct,
    STRING_AGG(d.document_type, ', ')
        FILTER (WHERE d.party = 'Customs'  AND d.status IS DISTINCT FROM 'Done') AS missing_customs_documents,
    STRING_AGG(d.document_type, ', ')
        FILTER (WHERE d.party = 'Customer' AND d.status IS DISTINCT FROM 'Done') AS missing_customer_documents,
    STRING_AGG(d.document_type, ', ')
        FILTER (WHERE d.party = 'Bank'     AND d.status IS DISTINCT FROM 'Done') AS missing_bank_documents
FROM exports e
LEFT JOIN export_documents d USING (export_id)
GROUP BY e.export_id, e.exp_no, e.batch_no;'''


#-------------------------- SHIPMENTS SHEET VIEW ---------------------------
shipments_view = '''CREATE OR REPLACE VIEW v_shipment_metrics AS
SELECT
    s.shipment_id,
    s.export_id,
    (s.actual_arrival_date - s.etd_karachi)                       AS transit_days,
    (s.port_in_date - s.qfl_arrival_date)                         AS qfl_stayed_days,
    (s.actual_sea_freight - s.quoted_sea_freight)                 AS freight_variance,
    COALESCE(s.actual_sea_freight,0) + COALESCE(s.lhr_khi_cost,0)
      + COALESCE(s.fumigation_cost,0) + COALESCE(s.lashing_cost,0)
      + COALESCE(s.qfl_port_cost,0)  + COALESCE(s.qfl_cost,0)
      + COALESCE(s.clearance_cost,0) + COALESCE(s.dhl_charges,0)
      + COALESCE(s.insurance,0)      + COALESCE(s.wharfage,0)     AS total_logistics_cost,
    CASE WHEN s.gross_weight_kgs > 0 THEN
        ROUND((COALESCE(s.actual_sea_freight,0) + COALESCE(s.lhr_khi_cost,0)
             + COALESCE(s.fumigation_cost,0) + COALESCE(s.lashing_cost,0)
             + COALESCE(s.qfl_port_cost,0)  + COALESCE(s.qfl_cost,0)
             + COALESCE(s.clearance_cost,0) + COALESCE(s.dhl_charges,0)
             + COALESCE(s.insurance,0)      + COALESCE(s.wharfage,0))
             / s.gross_weight_kgs, 2)
    END                                                            AS cost_per_kg
FROM export_shipments s;'''


#----------------------- PACKING SHEET VIEW ---------------------------------
packing_view = '''CREATE OR REPLACE VIEW v_packing_metrics AS
SELECT
    p.packing_id,
    p.export_id,
    (p.actual_packing_date - p.target_packing_date)               AS packing_delay_days,
    (p.actual_rfd_date - p.target_rfd)                            AS rfd_delay_days,
    (p.actual_packing_material_mfg_date
       - p.target_packing_material_mfg_date)                      AS material_mfg_delay_days,
    (p.actual_packing_date <= p.target_packing_date)              AS on_time_packing,
    (p.actual_packing_cost - p.quoted_packing_cost)               AS packing_cost_variance,
    CASE WHEN p.quoted_packing_cost > 0 THEN
        ROUND(100.0 * (p.actual_packing_cost - p.quoted_packing_cost)
              / p.quoted_packing_cost, 1)
    END                                                            AS cost_variance_pct
FROM packing_details p;'''


#----------------------- SHIFTING MOVEMENT SHEET VIEW ------------------------
shifting_movement_view = '''CREATE OR REPLACE VIEW v_shifting_metrics AS
SELECT
    m.shifting_id,
    (m.quoted_freight_rs - m.actual_freight_rs)                   AS savings_rs,
    CASE WHEN m.quoted_freight_rs > 0 THEN
        ROUND(100.0 * (m.quoted_freight_rs - m.actual_freight_rs)
              / m.quoted_freight_rs, 1)
    END                                                            AS savings_pct,
    (m.actual_freight_rs - m.quoted_freight_rs)                   AS freight_variance,
    CASE WHEN m.total_gross_weight_kgs > 0 THEN
        ROUND(m.actual_freight_rs / m.total_gross_weight_kgs, 2)
    END                                                            AS rate_per_kg,
    (m.eta_destination - m.execution_date)                        AS transit_days
FROM shifting_movements m;'''

logistics_views_queries = [exports_view, shipments_view, packing_view, shifting_movement_view]