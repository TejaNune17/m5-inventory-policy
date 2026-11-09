-- Data marts read by the Power BI dashboard. Every dashboard number comes from one of these tables.

-- Headline KPIs: base scenario (cell service targets, L = 7, sigma_L = 1), one row per policy.
CREATE OR REPLACE TABLE mart_kpi AS
SELECT policy, n_series, units_demanded, units_sold, units_lost, fill_rate, cycle_service_level,
       stockout_days, n_orders, avg_inventory_value, holding_cost, order_cost, lost_margin,
       lost_sales_value, total_cost, cost_per_unit_sold, cost_delta_vs_naive
FROM policy_summary
WHERE scenario = 'base'
ORDER BY policy;

-- ABC-XYZ matrix on the full dataset, with sample counts and assumed service targets.
CREATE OR REPLACE TABLE mart_abc_xyz AS
SELECT m.abc, m.xyz, m.abc || m.xyz AS cell, m.series, m.series_share, m.revenue, m.revenue_share,
       coalesce(s.sample_series, 0) AS sample_series,
       t.target_service_level
FROM seg_matrix m
LEFT JOIN (SELECT abc, xyz, count(*) AS sample_series FROM sample_series GROUP BY abc, xyz) s USING (abc, xyz)
LEFT JOIN (SELECT DISTINCT cell, target_service_level FROM seg_abc_xyz) t ON t.cell = m.abc || m.xyz
ORDER BY m.abc, m.xyz;

-- Forecast accuracy at series x fold x model grain. WAPE and Bias are re-aggregated in DAX from the sums.
CREATE OR REPLACE TABLE mart_forecast_accuracy AS
SELECT d.model, d.origin AS fold_origin_d, c.date AS fold_start_date, d.series_idx, s.item_id, s.store_id,
       d.abc, d.xyz, d.cell, d.cat_id, d.sum_abs_err, d.sum_err, d.sum_actual, d.mae, d.mase_scale, d.mase
FROM forecast_eval_detail d
JOIN sample_series s USING (series_idx)
JOIN dim_calendar c ON c.d = d.origin;

-- Pre-aggregated accuracy (same numbers as the DAX measures; used for docs and QA).
CREATE OR REPLACE TABLE mart_forecast_summary AS
SELECT * FROM forecast_accuracy;

-- Simulation results at series x policy x scenario grain.
CREATE OR REPLACE TABLE mart_policy_results AS
SELECT r.scenario, sc.kind AS scenario_kind, sc.service_level AS scenario_service_level,
       sc.lead_time, sc.lead_time_sd, r.policy, r.series_idx, r.item_id, r.store_id, r.cat_id, r.abc, r.xyz, r.cell,
       r.unit_cost, r.units_demanded, r.units_sold, r.units_lost, r.fill_rate, r.cycles_ok, r.cycles_evaluated,
       r.cycle_service_level, r.stockout_days, r.n_orders, r.avg_on_hand, r.avg_inventory_value,
       r.holding_cost, r.order_cost, r.lost_margin, r.lost_sales_value, r.total_cost, r.revenue
FROM policy_results r
JOIN sim_scenarios sc USING (scenario);

-- Service level vs cost curve: uniform service-level sweep, one row per policy and target.
CREATE OR REPLACE TABLE mart_service_cost_curve AS
SELECT sc.service_level AS target_service_level, p.policy, p.fill_rate, p.cycle_service_level,
       p.avg_inventory_value, p.holding_cost, p.order_cost, p.lost_margin, p.total_cost, p.cost_per_unit_sold,
       p.cost_delta_vs_naive
FROM policy_summary p
JOIN sim_scenarios sc USING (scenario)
WHERE sc.kind = 'service_level'
ORDER BY p.policy, target_service_level;
