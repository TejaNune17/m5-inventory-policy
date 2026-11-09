# Assumptions

Every value below is set in `config/assumptions.yaml`. None are measured. Change the config, rerun `make all`.

## Data and windows

| Key | Value | Justification |
|---|---|---|
| `calendar.last_day` | 1941 | Last day in `sales_train_evaluation.csv` (2016-05-22). |
| `calendar.horizon_days` | 28 | M5 forecast horizon. Also the P2 recompute cadence. |
| `calendar.sim_blocks` | 13 | 13 x 28 = 364 days, about one year of simulation. Aligns with the 28-day recompute cadence. |
| `calendar.warmup_blocks` | 3 | Gives P2 out-of-sample forecast errors for its first block. |
| `calendar.eval_folds` | 4 | Spec: 4 rolling-origin folds, final fold ends at d_1941. |
| `calendar.segment_window_days` | 364 | Spec: last 52 weeks of training data. |
| `random_seed` | 42 | Spec. Used for sampling, LightGBM, and lead-time draws. |

## Data quality

| Key | Value | Justification |
|---|---|---|
| `dq.price_change_threshold` | 0.5 | Spec: flag week-over-week price change above 50%. |
| `dq.long_zero_run_days` | 28 | A zero run of 4 weeks or more in an otherwise selling series is more likely a stockout or delisting than random zero demand. Flag only. |

## Segmentation

| Key | Value | Justification |
|---|---|---|
| `segmentation.abc_cutoffs` | A <= 80%, B <= 95% of cumulative revenue | Spec. Standard Pareto split. |
| `segmentation.xyz_cutoffs` | X < 0.5, Y 0.5 to 1.0, Z > 1.0 CV of weekly units | Spec. Common thresholds for weekly CV. |
| `segmentation.sample_size` | 1000 | Spec. Keeps forecasting and simulation runtime manageable. |
| `service_level_targets` | AX 98%, AY 97%, AZ 95%, BX 96%, BY 95%, BZ 93%, CX 92%, CY 90%, CZ 85% | Spec. Assumed policy targets: protect high-revenue, predictable items most; accept lower service on low-value erratic items. Not derived from data. |

## Forecasting

| Key | Value | Justification |
|---|---|---|
| `forecast.ets_max_zero_share` | 0.5 | ETS on a series that is mostly zeros produces unstable fits; above half zero days, use the 28-day moving average. |
| `forecast.ets_fit_window_days` | 728 | Two years captures weekly seasonality and recent level without long fits. |
| `forecast.lgb_train_window_days` | 1092 | Three years of history per origin. Bounds training time across 16 origins. |
| `forecast.lgb_params` | tweedie, power 1.1, lr 0.05, 63 leaves | Tweedie suits non-negative intermittent counts. Values are common defaults, not tuned. |
| `forecast.lgb_num_boost_round` | 400 | Fixed, no early stopping, to keep folds identical and avoid using test data for tuning. |

## Inventory simulation

| Key | Value | Justification |
|---|---|---|
| `inventory.lead_time_days` | 7 | Spec. Base supplier lead time. |
| `inventory.lead_time_sd_days` | 1 | Spec. Base lead time variability. |
| `inventory.review_period_days` | 1 | Spec. Continuous review approximated by daily review. |
| `inventory.unit_cost_share_of_price` | 0.70 | Spec. Unit cost = 70% of average sell price over the 52 weeks before simulation. Implies a 30% gross margin. |
| `inventory.annual_holding_rate` | 0.25 | Spec. 25% of unit cost per year, a common textbook value covering capital, storage, and shrink. |
| `inventory.order_cost` | 20.0 | Spec. Fixed cost per order in dollars. |
| `inventory.stockout_mode` | lost_sales | Spec. Retail shoppers rarely backorder a grocery or household item; unmet demand is lost. |
| `inventory.naive_window_days` | 90 | Spec. P0 uses the trailing 90 days. |
| `inventory.p2_error_window_blocks` | 3 | P2 error std from the prior 84 days of out-of-sample errors. Short enough to adapt, long enough to be stable. |
| `inventory.days_per_year` | 365 | Converts annual holding rate and demand. |
| Initial inventory | reorder point, rounded up | Spec. |
| Lost-sales cost | units lost x (price - unit cost) | Lost margin, not lost revenue, is the economic cost. Lost revenue is reported separately as `lost_sales_value`. |

## Scenarios

| Key | Value | Justification |
|---|---|---|
| `scenarios.service_level_sweep` | 85% to 99.5% | Spec. Uniform target applied to all series to trace the cost curve. |
| `scenarios.lead_times` x `scenarios.lead_time_sds` | {3, 7, 14} x {0, 1, 3} | Spec. Full grid. |
