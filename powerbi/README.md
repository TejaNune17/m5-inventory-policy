# Power BI build guide

The pipeline writes every table the dashboard needs to `powerbi/data/` (`make report`). The `.pbix` is built by hand in Power BI Desktop by following these steps. Every visual reads a mart; no numbers are typed in.

## 1. Load data

Home > Get data > Text/CSV. Load each file in `powerbi/data/`:

| Table | Grain | Used on |
|---|---|---|
| `mart_kpi` | policy, base scenario | Page 1 |
| `mart_policy_results` | scenario x policy x series | Pages 1, 4 |
| `mart_service_cost_curve` | policy x target service level | Page 4 |
| `mart_abc_xyz` | ABC x XYZ cell, all series | Page 2 |
| `mart_forecast_accuracy` | model x fold x series | Page 3 |
| `mart_forecast_summary` | model x grouping, pre-aggregated | QA only |
| `dim_item` | item | slicers |
| `dim_store` | store | slicers |
| `dim_calendar` | day | fold dates |

In Power Query set `scenario_service_level`, `target_service_level`, and every cost and rate column to Decimal Number.

## 2. Model (star schema)

Facts: `mart_policy_results`, `mart_forecast_accuracy`. Dimensions: `dim_item`, `dim_store`, `dim_calendar`.

Relationships (single direction, many-to-one):
- `mart_policy_results[item_id]` -> `dim_item[item_id]`
- `mart_policy_results[store_id]` -> `dim_store[store_id]`
- `mart_forecast_accuracy[item_id]` -> `dim_item[item_id]`
- `mart_forecast_accuracy[store_id]` -> `dim_store[store_id]`
- `mart_forecast_accuracy[fold_start_date]` -> `dim_calendar[date]`

`mart_kpi`, `mart_abc_xyz`, and `mart_service_cost_curve` are small standalone summary tables with no relationships.

## 3. Measures

Create a blank table `_Measures` and add each measure from `measures.dax`.

## 4. Pages

### Page 1: Executive summary
- Slicer: `mart_policy_results[scenario]`, single select, default `base`.
- Cards: Fill Rate, Cycle Service Level, Avg Inventory Value, Total Cost, Cost Delta vs Naive %. Filter each card to policy `P2_forecast`, or put the cards in a small multiple by policy.
- Clustered bar: axis `policy`, values Total Cost split into Holding Cost, Order Cost, Lost Margin (stacked).
- Table: `mart_kpi`, all columns. This is the reference copy of the headline numbers.

### Page 2: Segmentation
- Matrix: rows `mart_abc_xyz[abc]`, columns `xyz`, values `series`. Second matrix with `revenue_share` formatted as a percentage.
- Table: `cell`, `target_service_level`, `sample_series`.
- Text box: "Service targets per cell are assumptions, not measured."

### Page 3: Forecast accuracy
- Slicers: `cat_id`, `abc`, `xyz`.
- Clustered column: axis `model`, value WAPE.
- Clustered column: axis `xyz`, legend `model`, value WAPE.
- Table: `model`, WAPE, Bias, MASE.
- Line: axis `fold_start_date`, legend `model`, value WAPE.

### Page 4: Policy simulation
- Line chart: x `mart_service_cost_curve[target_service_level]`, y `total_cost`, legend `policy`.
- Scatter: x `fill_rate`, y `total_cost`, legend `policy`, same table.
- What-if parameter `Target SL` (see `measures.dax`), shown as a slider. Cards: Selected SL Total Cost and Selected SL Fill Rate, by policy.
- Lead-time slicers: `mart_policy_results[lead_time]` and `lead_time_sd`, with `scenario_kind` filtered to `lead_time` or `base`. Clustered column: axis `policy`, values Total Cost and Fill Rate.

## 5. Screenshots

Export each page (File > Export > PDF, or a screenshot tool) to `powerbi/screenshots/` as `01_executive.png`, `02_segmentation.png`, `03_forecast.png`, `04_simulation.png`. The repo README links the first one.
