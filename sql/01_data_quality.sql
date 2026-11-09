-- Data quality checks. Produces table dq_results(section, check_name, value, treatment).
-- Placeholders: ${price_change_threshold} (fraction), ${long_zero_run_days} (int).

CREATE OR REPLACE TEMP TABLE zero_runs AS
WITH z AS (
    SELECT s.item_id, s.store_id, s.d, ds.last_sale_d,
           s.d - row_number() OVER (PARTITION BY s.item_id, s.store_id ORDER BY s.d) AS grp
    FROM fact_sales s
    JOIN dim_series ds USING (item_id, store_id)
    WHERE ds.first_sale_d IS NOT NULL AND s.d > ds.first_sale_d AND s.units = 0
)
SELECT item_id, store_id, min(d) AS start_d, max(d) AS end_d, count(*) AS run_len,
       CASE WHEN min(d) > max(last_sale_d) THEN 'trailing' ELSE 'mid' END AS run_type
FROM z
GROUP BY item_id, store_id, grp;

CREATE OR REPLACE TEMP TABLE price_changes AS
SELECT store_id, item_id, wm_yr_wk, sell_price,
       sell_price / lag(sell_price) OVER (PARTITION BY store_id, item_id ORDER BY wm_yr_wk) - 1 AS pct_change
FROM fact_price;

CREATE OR REPLACE TABLE dq_results AS
-- 1. Reconciliation ---------------------------------------------------------
SELECT '1 reconciliation' AS section, 'sales: source series rows' AS check_name,
       (SELECT data_rows FROM src_file_counts WHERE file = 'sales_train_evaluation.csv')::DOUBLE AS value,
       'reference' AS treatment
UNION ALL SELECT '1 reconciliation', 'sales: loaded distinct series',
       (SELECT count(*) FROM dim_series)::DOUBLE, 'must equal source series rows'
UNION ALL SELECT '1 reconciliation', 'sales: expected long rows (series x d columns)',
       (SELECT data_rows * d_columns FROM src_file_counts WHERE file = 'sales_train_evaluation.csv')::DOUBLE, 'reference'
UNION ALL SELECT '1 reconciliation', 'sales: loaded fact_sales rows',
       (SELECT count(*) FROM fact_sales)::DOUBLE, 'must equal expected long rows'
UNION ALL SELECT '1 reconciliation', 'sales: units total in fact_sales',
       (SELECT sum(units) FROM fact_sales)::DOUBLE, 'reference'
UNION ALL SELECT '1 reconciliation', 'calendar: source rows',
       (SELECT data_rows FROM src_file_counts WHERE file = 'calendar.csv')::DOUBLE, 'reference'
UNION ALL SELECT '1 reconciliation', 'calendar: loaded rows',
       (SELECT count(*) FROM dim_calendar)::DOUBLE, 'must equal source rows'
UNION ALL SELECT '1 reconciliation', 'prices: source rows',
       (SELECT data_rows FROM src_file_counts WHERE file = 'sell_prices.csv')::DOUBLE, 'reference'
UNION ALL SELECT '1 reconciliation', 'prices: loaded rows',
       (SELECT count(*) FROM fact_price)::DOUBLE, 'must equal source rows'
-- 2. Duplicate keys ---------------------------------------------------------
UNION ALL SELECT '2 duplicates', 'fact_sales duplicate (item_id, store_id, d) keys',
       (SELECT count(*) FROM (SELECT 1 FROM fact_sales GROUP BY item_id, store_id, d HAVING count(*) > 1))::DOUBLE,
       'none expected; would be excluded'
UNION ALL SELECT '2 duplicates', 'fact_price duplicate (store_id, item_id, wm_yr_wk) keys',
       (SELECT count(*) FROM (SELECT 1 FROM fact_price GROUP BY store_id, item_id, wm_yr_wk HAVING count(*) > 1))::DOUBLE,
       'none expected; would be excluded'
-- 3. Nulls --------------------------------------------------------------------
UNION ALL SELECT '3 nulls', 'fact_sales null item_id/store_id/d/date',
       (SELECT count(*) FROM fact_sales WHERE item_id IS NULL OR store_id IS NULL OR d IS NULL OR date IS NULL)::DOUBLE, 'none expected'
UNION ALL SELECT '3 nulls', 'fact_sales null units',
       (SELECT count(*) FROM fact_sales WHERE units IS NULL)::DOUBLE, 'none expected'
UNION ALL SELECT '3 nulls', 'fact_sales null sell_price (all rows)',
       (SELECT count(*) FROM fact_sales WHERE sell_price IS NULL)::DOUBLE, 'see section 5'
UNION ALL SELECT '3 nulls', 'fact_price null sell_price',
       (SELECT count(*) FROM fact_price WHERE sell_price IS NULL)::DOUBLE, 'none expected'
UNION ALL SELECT '3 nulls', 'dim_calendar null event_name_1 (days without event)',
       (SELECT count(*) FROM dim_calendar WHERE event_name_1 IS NULL)::DOUBLE, 'kept; null means no event'
-- 4. Negative units -------------------------------------------------------------
UNION ALL SELECT '4 negative units', 'fact_sales rows with units < 0',
       (SELECT count(*) FROM fact_sales WHERE units < 0)::DOUBLE, 'none expected; would be excluded'
-- 5. Missing prices ---------------------------------------------------------------
UNION ALL SELECT '5 missing price', 'rows with units > 0 and no price',
       (SELECT count(*) FROM fact_sales WHERE units > 0 AND sell_price IS NULL)::DOUBLE,
       'flagged; revenue counted as 0, simulation forward/back-fills price within series'
UNION ALL SELECT '5 missing price', 'pre-launch rows with no price',
       (SELECT count(*) FROM fact_sales s JOIN dim_series ds USING (item_id, store_id)
         WHERE s.sell_price IS NULL AND (ds.first_sale_d IS NULL OR s.d < ds.first_sale_d))::DOUBLE,
       'expected (item not yet listed); rows excluded as pre-launch'
UNION ALL SELECT '5 missing price', 'post-launch zero-unit rows with no price',
       (SELECT count(*) FROM fact_sales s JOIN dim_series ds USING (item_id, store_id)
         WHERE s.sell_price IS NULL AND s.d >= ds.first_sale_d AND s.units = 0)::DOUBLE,
       'kept; price filled within series'
-- 6. Zero runs --------------------------------------------------------------------
UNION ALL SELECT '6 zero runs', 'series with no sales in full history',
       (SELECT count(*) FROM dim_series WHERE first_sale_d IS NULL)::DOUBLE, 'excluded'
UNION ALL SELECT '6 zero runs', 'series with leading (pre-launch) zero run',
       (SELECT count(*) FROM dim_series WHERE first_sale_d > 1)::DOUBLE, 'leading zeros excluded'
UNION ALL SELECT '6 zero runs', 'leading (pre-launch) zero days, total (series with sales)',
       (SELECT coalesce(sum(first_sale_d - 1), 0) FROM dim_series WHERE first_sale_d IS NOT NULL)::DOUBLE,
       'excluded'
UNION ALL SELECT '6 zero runs', 'leading zero days as share of all fact_sales rows',
       ((SELECT coalesce(sum(first_sale_d - 1), 0) FROM dim_series WHERE first_sale_d IS NOT NULL)
        / (SELECT count(*) FROM fact_sales))::DOUBLE,
       'excluded'
UNION ALL SELECT '6 zero runs', 'mid-series zero runs, total',
       (SELECT count(*) FROM zero_runs WHERE run_type = 'mid')::DOUBLE, 'kept as zero demand'
UNION ALL SELECT '6 zero runs', 'mid-series zero runs, days total',
       (SELECT coalesce(sum(run_len), 0) FROM zero_runs WHERE run_type = 'mid')::DOUBLE, 'kept as zero demand'
UNION ALL SELECT '6 zero runs', 'mid-series zero runs >= ${long_zero_run_days} days',
       (SELECT count(*) FROM zero_runs WHERE run_type = 'mid' AND run_len >= ${long_zero_run_days})::DOUBLE,
       'flagged as possible stockout or delisting; kept as zero demand (sales-only data cannot separate the two)'
UNION ALL SELECT '6 zero runs', 'series with at least one mid-series zero run >= ${long_zero_run_days} days',
       (SELECT count(DISTINCT (item_id, store_id)) FROM zero_runs WHERE run_type = 'mid' AND run_len >= ${long_zero_run_days})::DOUBLE,
       'flagged; kept'
UNION ALL SELECT '6 zero runs', 'trailing zero runs (no sale after run start until d_last)',
       (SELECT count(*) FROM zero_runs WHERE run_type = 'trailing')::DOUBLE, 'kept as zero demand'
UNION ALL SELECT '6 zero runs', 'trailing zero days, total',
       (SELECT coalesce(sum(run_len), 0) FROM zero_runs WHERE run_type = 'trailing')::DOUBLE, 'kept as zero demand'
-- 7. Zero-sales share (post-launch) ---------------------------------------------
UNION ALL SELECT '7 zero share', 'share of zero-sales days, all categories (post-launch)',
       (SELECT avg(CASE WHEN units = 0 THEN 1.0 ELSE 0.0 END) FROM v_sales_clean), 'informs metric choice (no MAPE)'
UNION ALL
SELECT * FROM (
    SELECT '7 zero share', 'share of zero-sales days, ' || i.cat_id || ' (post-launch)',
           avg(CASE WHEN s.units = 0 THEN 1.0 ELSE 0.0 END), 'informs metric choice (no MAPE)'
    FROM v_sales_clean s JOIN dim_item i USING (item_id)
    GROUP BY i.cat_id
    ORDER BY i.cat_id
)
-- 8. Price outliers ---------------------------------------------------------------
UNION ALL SELECT '8 price outliers', 'week-over-week price change > ${price_change_threshold} (rows)',
       (SELECT count(*) FROM price_changes WHERE abs(pct_change) > ${price_change_threshold})::DOUBLE,
       'flagged; kept (promotions and corrections are real prices)'
UNION ALL SELECT '8 price outliers', 'week-over-week price change > ${price_change_threshold} (distinct series)',
       (SELECT count(DISTINCT (item_id, store_id)) FROM price_changes WHERE abs(pct_change) > ${price_change_threshold})::DOUBLE,
       'flagged; kept';
