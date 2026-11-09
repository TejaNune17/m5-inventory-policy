# Methodology

Constants are in `config/assumptions.yaml` and justified in `docs/assumptions.md`. Judgment calls are in `docs/decisions.md`. Results are in `docs/case_study.md` and the marts.

## Pipeline

| Step | Code | Output |
|---|---|---|
| Ingest | `src/ingest.py`, `sql/00_schema.sql` | `fact_sales` (long, one row per item-store-day), `fact_price`, `dim_calendar`, `dim_item`, `dim_store`, `dim_series`, `v_sales_clean` |
| Data quality | `src/dq.py`, `sql/01_data_quality.sql` | `dq_results`, `docs/dq_report.md` |
| Segmentation | `src/segment.py`, `sql/02_abc_xyz.sql` | `seg_abc_xyz`, `seg_matrix`, `sample_series` |
| Forecasting | `src/forecast.py` | `forecasts`, `fold_log`, `forecast_eval_detail`, `forecast_accuracy` |
| Simulation | `src/simulate.py` | `sim_scenarios`, `policy_params`, `policy_results`, `policy_summary` |
| Marts | `src/report.py`, `sql/03_marts.sql` | `powerbi/data/*.csv`, `docs/figures/*.png` |

SQL does loading, data quality, segmentation, and marts. Python does forecasting and simulation, and independently recomputes the segmentation as a cross-check.

## Time layout

```
d_1 ............ d_1214 ........ d_1577 | d_1578 ........................... d_1941
                 [ segmentation 52 wk ] | [ simulation: 13 blocks x 28 days        ]
                                        |                [ accuracy: 4 folds x 28 ]
       LightGBM origins: d_1494, d_1522, d_1550 (warm-up), then every 28 days from d_1578
```

Nothing that defines a policy uses data from on or after the day the policy is applied.

## Segmentation

- ABC: series ranked by revenue (units x sell price) over the 52-week window. Cumulative share up to 80% is A, up to 95% is B, the rest C.
- XYZ: coefficient of variation of weekly units. Below 0.5 is X, 0.5 to 1.0 is Y, above 1.0 is Z.
- Each cell gets an assumed target cycle service level.
- Sample: 1,000 series, stratified by ABC x XYZ x category, proportional allocation, seed 42.

## Forecasting

Rolling origin with 4 folds, each forecasting 28 days. Models:

1. Naive: last observed value.
2. Seasonal naive: the last 7 days repeated.
3. Moving average: mean of the last 28 days.
4. ETS: Holt-Winters, additive weekly seasonality, no trend. Falls back to the moving average on sparse series.
5. LightGBM: one global tweedie model across all sampled series, forecasting recursively. Features: lags 7/14/28; rolling mean and std over 7 and 28 days; day of week; month; event flag; SNAP flag; sell price; price change vs 4-week average; item, store, department, category, and state codes.

Metrics: WAPE, bias, and MASE (seasonal period 7, scaled by in-sample seasonal naive error). MAPE is not used because most series have zero-sales days, where percentage error is undefined.

## Inventory simulation

Continuous review (s, Q) checked daily, with lost sales. Run per series over the 364-day simulation window against actual daily demand.

- Safety stock: SS = z x sqrt(L x sigma_d^2 + d_bar^2 x sigma_L^2)
- Reorder point: ROP = d_bar x L + SS
- Order quantity: Q = EOQ = sqrt(2 D S / H), rounded up

| Policy | d_bar and sigma_d | Update |
|---|---|---|
| P0 naive | trailing 90 days before simulation | never |
| P1 static | full post-launch history before simulation | never |
| P2 forecast | LightGBM 28-day forecast mean; std of out-of-sample LightGBM errors over prior 84 days | every 28 days |

Daily loop:
1. Receive arrivals.
2. Serve demand from on-hand stock and record lost units.
3. If on-hand plus on-order is at or below ROP, order Q with a sampled lead time.
4. Accrue holding cost on end-of-day on-hand.

Costs: holding (25% of unit cost per year), ordering ($20 per order), and lost margin (price minus unit cost per lost unit).

Scenarios:
- **Base:** cell service targets, L = 7, sigma_L = 1.
- **Service-level sweep:** a uniform target from 85% to 99.5%.
- **Lead-time grid:** L in {3, 7, 14} x sigma_L in {0, 1, 3}.

## Limitation

M5 contains sales, not stock on hand. Inventory is simulated against observed sales treated as demand. Observed sales are censored wherever the real store was out of stock, so true demand is understated on those days.

## Tests

`pytest` covers:
- Every metric against hand-computed values.
- The simulation engine against a hand-traced 10-day example.
- Non-negative inventory and reproducibility.
- Leakage: training rows strictly precede the origin, and changing future values does not change features or LightGBM forecasts.
