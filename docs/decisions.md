# Decisions

Choices made where the spec was ambiguous. Each is the simplest defensible option.

## D1. Time layout
All windows are derived from config in `src/common.py`:
- Simulation window: the final 364 days, d_1578 to d_1941 (13 blocks of 28 days). 364 instead of 365 so every block aligns with the 28-day forecast cadence.
- Segmentation window: the 52 weeks before simulation, d_1214 to d_1577. Segmentation, the sample, unit costs, and P0/P1 parameters use only data before the simulation starts. "Training data" for segmentation means data before the simulation window.
- Forecast accuracy folds: origins d_1830, d_1858, d_1886, d_1914 (4 x 28 days, last ends at d_1941). These sit inside the simulation window.
- LightGBM is trained at 16 origins: 3 warm-up origins (d_1494, d_1522, d_1550) plus the 13 simulation blocks. Training at each origin uses only days before it.

## D2. Pre-launch treatment
A series' first day with units > 0 is its launch. Earlier days are excluded (`v_sales_clean`). Series that never sell are excluded. Mid-series and trailing zero runs are kept as zero demand.

## D3. Prices on fact_sales
`fact_sales.sell_price` is joined from `fact_price` on (store, item, wm_yr_wk). Missing prices count as zero revenue in segmentation. In forecasting and simulation, prices are forward-filled, then back-filled, within each series.

## D4. ABC-XYZ details
- ABC uses inclusive cumulative revenue share after sorting by revenue descending, ties broken by item_id then store_id. Series with zero revenue in the window are C.
- XYZ uses the population standard deviation (ddof = 0) of weekly units. Weeks are 7-day buckets counted from the window start, not Walmart weeks, so all 52 are complete. Series with zero mean are Z.
- Python and SQL compute this independently; `segment.py` fails if they disagree on any series.
- CV is rounded to 10 decimals in both before classifying. The first run disagreed on 2 series whose CV was exactly 0.5 or 1.0; SQL and pandas differed in the last floating-point bit. Rounding makes boundary ties classify identically.
- No ABC-XYZ cell is empty with the spec thresholds, so they were not changed.

## D5. Sample
- Eligible: launched on or before the segmentation window start (d_1214) and sold at least one unit in the window. This guarantees at least 280 days of history before the first LightGBM origin.
- Strata: ABC x XYZ x category (27 strata). Proportional allocation with largest-remainder rounding and at least one series per non-empty stratum. Within a stratum, simple random sampling with `numpy.random.default_rng(42)`.

## D6. Forecasting
- Baselines and ETS are refit at each evaluation origin on the series' own history.
- ETS is Holt-Winters with additive weekly seasonality and no trend, fit on daily data. It falls back to the 28-day moving average when over half the fit window is zero or when the fit fails. The fallback method is recorded per forecast in `forecasts.method`.
- LightGBM forecasts recursively: each predicted day is written back and used as a lag for later horizon days. Future actuals are set to NaN before prediction and an assertion fails if any lag feature reads them.
- Sell prices on forecast days are treated as known (planned prices), the M5 convention.
- Aggregate MASE is the mean of per-series-fold MASE. Series with a zero in-sample seasonal naive error have undefined MASE; their count is reported.

## D7. Simulation
- Order placed at the end of day t with lead time L arrives at the start of day t + L + 1, so each order covers L days of demand exposure, consistent with ROP = d_bar x L + SS.
- Sampled lead time = max(1, round(L + sigma_L x N(0, 1))). Truncation at 1 day slightly raises the mean lead time when sigma_L is large relative to L.
- At most one order of size Q per day.
- Q = EOQ rounded up, minimum 1 unit. ROP is not rounded.
- Cycle service level: share of orders with no lost sales during their lead-time window (days t+1 to t+L). Orders whose window extends past the simulation end are excluded.
- P0 and P1 use the same formulas. They differ only in the estimation window (90 days vs full history). This isolates the effect of the window, and P2 isolates the effect of the forecast.
- P2 sigma_d is the population std of the daily out-of-sample LightGBM errors over the 3 prior blocks. It is a std, not RMSE, so forecast bias is not added to safety stock.
- Lead-time draws use one RNG stream per series (`default_rng([42, series_idx])`), the same for every policy.
- Total cost = holding + ordering + lost margin. Orders placed near the end of the window incur order cost even if they arrive after it.
- P0 is left as specified even where it breaks: a series with no sales in the trailing 90 days gets d_bar = 0, so Q = 1 and ROP = 0. This is the naive rule's real failure mode on erratic items, so it is kept rather than patched.
- With the assumed $20 order cost and low unit costs, EOQ yields few orders per series per year. Cycle service level is therefore measured on few cycles per series; pooled CSL across series is reported, not per-series CSL.

## D8. Marts for Power BI
Mart CSVs are written to `powerbi/data/` and committed, so the dashboard can be built without rerunning the pipeline.
