# Data

Raw data is not committed. Source: Kaggle competition [M5 Forecasting - Accuracy](https://www.kaggle.com/competitions/m5-forecasting-accuracy).

1. Accept the competition rules on Kaggle (Rules tab, "I Understand and Accept").
2. Create an API token (kaggle.com > Settings > API) and save it as `~/.kaggle/access_token` (new token format) or `~/.kaggle/kaggle.json` (legacy).
3. Run `make data`. It downloads the files below into `data/raw/` and loads `data/warehouse.duckdb`.

Alternatively, download the three files manually from the competition's Data tab into `data/raw/`; `make data` skips the download when they exist.

| File | Content |
|---|---|
| `calendar.csv` | date, weekday, month, year, events, SNAP flags per state |
| `sales_train_evaluation.csv` | daily unit sales, one row per item-store, columns `d_1` to `d_1941` |
| `sell_prices.csv` | weekly sell price per store and item |

`data/processed/` holds intermediate outputs (sample list, DuckDB spill files). Both `raw/` and `processed/` are git-ignored.
