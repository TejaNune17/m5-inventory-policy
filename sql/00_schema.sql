-- Load raw M5 CSVs into the DuckDB warehouse.
-- ${raw} is substituted by src/ingest.py with the raw data directory.

CREATE OR REPLACE TABLE dim_calendar AS
SELECT
    CAST(substr(d, 3) AS INTEGER)          AS d,
    CAST(date AS DATE)                     AS date,
    wm_yr_wk,
    weekday,
    wday,
    month,
    year,
    event_name_1,
    event_type_1,
    event_name_2,
    event_type_2,
    (event_name_1 IS NOT NULL)             AS is_event,
    snap_CA,
    snap_TX,
    snap_WI
FROM read_csv('${raw}/calendar.csv', header = true, auto_detect = true);

CREATE OR REPLACE TEMP TABLE stg_sales AS
SELECT * FROM read_csv('${raw}/sales_train_evaluation.csv', header = true, auto_detect = true);

CREATE OR REPLACE TABLE dim_item AS
SELECT DISTINCT item_id, dept_id, cat_id FROM stg_sales ORDER BY item_id;

CREATE OR REPLACE TABLE dim_store AS
SELECT DISTINCT store_id, state_id FROM stg_sales ORDER BY store_id;

CREATE OR REPLACE TABLE fact_price AS
SELECT store_id, item_id, wm_yr_wk, sell_price
FROM read_csv('${raw}/sell_prices.csv', header = true, auto_detect = true);

-- Wide to long. INCLUDE NULLS keeps the row count equal to series x days so reconciliation is exact.
CREATE OR REPLACE TABLE fact_sales AS
WITH long AS (
    SELECT item_id, store_id, d_col, units
    FROM stg_sales
    UNPIVOT INCLUDE NULLS (units FOR d_col IN (COLUMNS('^d_[0-9]+$')))
)
SELECT
    l.item_id,
    l.store_id,
    CAST(substr(l.d_col, 3) AS SMALLINT)   AS d,
    c.date,
    c.wm_yr_wk,
    CAST(l.units AS INTEGER)               AS units,
    p.sell_price
FROM long l
JOIN dim_calendar c
  ON c.d = CAST(substr(l.d_col, 3) AS INTEGER)
LEFT JOIN fact_price p
  ON p.store_id = l.store_id AND p.item_id = l.item_id AND p.wm_yr_wk = c.wm_yr_wk
ORDER BY l.store_id, l.item_id, d;

-- One row per item-store series with launch information.
CREATE OR REPLACE TABLE dim_series AS
SELECT
    s.item_id,
    s.store_id,
    i.dept_id,
    i.cat_id,
    st.state_id,
    min(CASE WHEN s.units > 0 THEN s.d END) AS first_sale_d,
    max(CASE WHEN s.units > 0 THEN s.d END) AS last_sale_d
FROM fact_sales s
JOIN dim_item i USING (item_id)
JOIN dim_store st USING (store_id)
GROUP BY ALL
ORDER BY s.store_id, s.item_id;

-- Treatment rule: pre-launch zeros (before the first sale) are excluded. Mid-series zeros are kept as zero demand.
CREATE OR REPLACE VIEW v_sales_clean AS
SELECT s.*
FROM fact_sales s
JOIN dim_series ds USING (item_id, store_id)
WHERE ds.first_sale_d IS NOT NULL AND s.d >= ds.first_sale_d;
