-- ABC-XYZ segmentation in SQL. src/segment.py computes the same in pandas and asserts both agree.
-- Placeholders: ${seg_start}, ${seg_end} (d indices), ${abc_a}, ${abc_b}, ${xyz_x}, ${xyz_y}.
-- Window: 52 weeks immediately before the simulation start. Pre-launch days are excluded (v_sales_clean).
-- Weeks are 7-day buckets counted from ${seg_start}, so every week in the window is complete.

CREATE OR REPLACE TABLE seg_abc_xyz_sql AS
WITH win AS (
    SELECT item_id, store_id, d, units, coalesce(sell_price, 0) AS price
    FROM v_sales_clean
    WHERE d BETWEEN ${seg_start} AND ${seg_end}
),
rev AS (
    SELECT item_id, store_id, sum(units * price) AS revenue, sum(units) AS units
    FROM win GROUP BY item_id, store_id
),
wk AS (
    SELECT item_id, store_id, (d - ${seg_start}) // 7 AS wk, sum(units) AS u
    FROM win GROUP BY item_id, store_id, wk
),
cv AS (
    SELECT item_id, store_id, avg(u) AS mean_weekly_units, stddev_pop(u) AS sd_weekly_units
    FROM wk GROUP BY item_id, store_id
),
base AS (
    SELECT ds.item_id, ds.store_id, ds.cat_id, ds.dept_id, ds.state_id, ds.first_sale_d,
           coalesce(r.revenue, 0) AS revenue,
           coalesce(r.units, 0)   AS units,
           coalesce(c.mean_weekly_units, 0) AS mean_weekly_units,
           c.sd_weekly_units
    FROM dim_series ds
    LEFT JOIN rev r USING (item_id, store_id)
    LEFT JOIN cv c USING (item_id, store_id)
),
ranked AS (
    SELECT *,
           sum(revenue) OVER (ORDER BY revenue DESC, item_id, store_id ROWS UNBOUNDED PRECEDING)
             / sum(revenue) OVER () AS cum_revenue_share
    FROM base
)
-- CV is rounded to 10 decimals so values exactly on a threshold classify identically in SQL and Python.
, with_cv AS (
    SELECT *, CASE WHEN mean_weekly_units > 0 THEN round(sd_weekly_units / mean_weekly_units, 10) END AS cv
    FROM ranked
)
SELECT item_id, store_id, cat_id, dept_id, state_id, first_sale_d, revenue, units,
       mean_weekly_units, sd_weekly_units, cv,
       cum_revenue_share,
       CASE WHEN revenue > 0 AND cum_revenue_share <= ${abc_a} THEN 'A'
            WHEN revenue > 0 AND cum_revenue_share <= ${abc_b} THEN 'B'
            ELSE 'C' END AS abc,
       CASE WHEN mean_weekly_units = 0 THEN 'Z'
            WHEN cv < ${xyz_x} THEN 'X'
            WHEN cv <= ${xyz_y} THEN 'Y'
            ELSE 'Z' END AS xyz
FROM with_cv
ORDER BY store_id, item_id;
