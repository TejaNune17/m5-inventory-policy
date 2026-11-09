"""Forecasting on the stratified sample: baselines, ETS, LightGBM global model, rolling origin.

- Accuracy is evaluated on `eval_origins` (4 folds of 28 days, last fold ends at d_1941).
- LightGBM is additionally trained at every 28-day origin covering the simulation window plus warm-up,
  because the forecast-driven policy (P2) needs out-of-sample forecasts and errors at each block.
- Training data at an origin uses only days strictly before the origin. fold_log records this and
  tests/test_forecast.py asserts it.

Outputs: tables forecasts, fold_log, forecast_eval_detail, forecast_accuracy; docs/figures/wape_by_model_xyz.png
"""
from __future__ import annotations

import warnings
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from common import connect, load_config, path, windows
import metrics

MODELS = ["naive", "snaive", "ma28", "ets", "lgbm"]
FEATURES = [
    "lag_7", "lag_14", "lag_28", "rmean_7", "rstd_7", "rmean_28", "rstd_28",
    "dow", "month", "is_event", "snap", "sell_price", "price_chg_4w",
    "item_code", "store_code", "dept_code", "cat_code", "state_code",
]
CATEGORICAL = ["item_code", "store_code", "dept_code", "cat_code", "state_code", "dow"]


# --- data ----------------------------------------------------------------------

@dataclass
class Panel:
    """Wide arrays for the sample. Column j is day d = j + 1."""
    Y: np.ndarray            # (n, T) units, NaN before first sale
    price: np.ndarray        # (n, T) sell price, filled within series
    snap: np.ndarray         # (n, T) SNAP flag for the series' state
    dow: np.ndarray          # (T,) 1..7
    month: np.ndarray        # (T,)
    is_event: np.ndarray     # (T,)
    static: pd.DataFrame     # n rows: series_idx, codes, abc, xyz, cat_id, first_sale_d

    @property
    def n(self) -> int:
        return self.Y.shape[0]


def load_panel(con, last_day: int) -> Panel:
    sample = con.execute("SELECT * FROM sample_series ORDER BY series_idx").df()
    n = len(sample)
    long = con.execute(
        f"""SELECT s.series_idx, f.d, f.units, f.sell_price
            FROM fact_sales f JOIN sample_series s USING (item_id, store_id)
            WHERE f.d <= {last_day}"""
    ).df()
    Y = np.full((n, last_day), np.nan)
    P = np.full((n, last_day), np.nan)
    Y[long.series_idx.to_numpy(), long.d.to_numpy() - 1] = long.units.to_numpy()
    P[long.series_idx.to_numpy(), long.d.to_numpy() - 1] = long.sell_price.to_numpy()
    first = sample["first_sale_d"].to_numpy().astype(int)
    for i in range(n):
        Y[i, : first[i] - 1] = np.nan
    P = pd.DataFrame(P).T.ffill().bfill().T.to_numpy()

    cal = con.execute(f"SELECT * FROM dim_calendar WHERE d <= {last_day} ORDER BY d").df()
    snap_by_state = {st: cal[f"snap_{st}"].to_numpy() for st in ["CA", "TX", "WI"]}
    snap = np.vstack([snap_by_state[s] for s in sample["state_id"]])

    static = sample.copy()
    for col, name in [("item_id", "item_code"), ("store_id", "store_code"), ("dept_id", "dept_code"),
                      ("cat_id", "cat_code"), ("state_id", "state_code")]:
        static[name] = static[col].astype("category").cat.codes.astype(int)
    return Panel(Y, P, snap, cal["wday"].to_numpy(), cal["month"].to_numpy(),
                 cal["is_event"].astype(int).to_numpy(), static)


# --- baselines -------------------------------------------------------------------

def history(panel: Panel, i: int, origin: int) -> np.ndarray:
    """Post-launch actuals for series i strictly before day `origin`."""
    y = panel.Y[i, : origin - 1]
    return y[~np.isnan(y)]


def fc_naive(h: np.ndarray, H: int) -> np.ndarray:
    return np.full(H, h[-1])


def fc_snaive(h: np.ndarray, H: int, m: int = 7) -> np.ndarray:
    return np.resize(h[-m:], H)


def fc_ma(h: np.ndarray, H: int, w: int = 28) -> np.ndarray:
    return np.full(H, h[-w:].mean())


def fc_ets(args) -> tuple[np.ndarray, str]:
    """Holt-Winters additive weekly seasonality, no trend. Falls back to 28-day MA."""
    h, H, max_zero_share = args
    if len(h) < 28 or (h == 0).mean() > max_zero_share:
        return fc_ma(h, H), "ma28_fallback_sparse"
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = ExponentialSmoothing(h, trend=None, seasonal="add", seasonal_periods=7,
                                       initialization_method="estimated").fit()
            f = np.asarray(fit.forecast(H), float)
        if not np.all(np.isfinite(f)):
            raise ValueError("non-finite forecast")
        return np.clip(f, 0, None), "ets"
    except Exception:
        return fc_ma(h, H), "ma28_fallback_error"


# --- LightGBM ----------------------------------------------------------------------

def features_for_day(panel: Panel, Yf: np.ndarray, d: int) -> dict[str, np.ndarray]:
    """Features for day d (1-based) for all series, using only Yf columns before d.
    Yf: units with pre-launch zeros as 0; columns >= d are never read."""
    t = d - 1
    past = Yf[:, :t]

    def win(w):
        return past[:, t - w:]

    p = panel.price[:, t]
    p_avg = panel.price[:, t - 28:t].mean(axis=1)
    st = panel.static
    return {
        "lag_7": past[:, t - 7], "lag_14": past[:, t - 14], "lag_28": past[:, t - 28],
        "rmean_7": win(7).mean(axis=1), "rstd_7": win(7).std(axis=1),
        "rmean_28": win(28).mean(axis=1), "rstd_28": win(28).std(axis=1),
        "dow": np.full(panel.n, panel.dow[t]), "month": np.full(panel.n, panel.month[t]),
        "is_event": np.full(panel.n, panel.is_event[t]), "snap": panel.snap[:, t],
        "sell_price": p, "price_chg_4w": p / p_avg - 1,
        "item_code": st["item_code"].to_numpy(), "store_code": st["store_code"].to_numpy(),
        "dept_code": st["dept_code"].to_numpy(), "cat_code": st["cat_code"].to_numpy(),
        "state_code": st["state_code"].to_numpy(),
    }


def training_frame(panel: Panel, origin: int, window_days: int) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Rows: (series, day) with origin - window_days <= day < origin and day >= first_sale + 28.
    Returns X, y, and the day of each row (for the leakage check)."""
    Yf = np.nan_to_num(panel.Y[:, : origin - 1], nan=0.0)   # nothing at or after origin is visible
    first = panel.static["first_sale_d"].to_numpy()
    Xs, ys, ds = [], [], []
    for d in range(max(29, origin - window_days), origin):
        ok = d >= first + 28
        if not ok.any():
            continue
        f = features_for_day(panel, Yf, d)
        Xs.append(pd.DataFrame({k: v[ok] for k, v in f.items()}))
        ys.append(Yf[ok, d - 1])
        ds.append(np.full(ok.sum(), d))
    return pd.concat(Xs, ignore_index=True), np.concatenate(ys), np.concatenate(ds)


def lgbm_origin(panel: Panel, origin: int, H: int, cfg: dict) -> tuple[np.ndarray, dict]:
    import lightgbm as lgb
    fc = cfg["forecast"]
    X, y, days = training_frame(panel, origin, fc["lgb_train_window_days"])
    params = dict(fc["lgb_params"], seed=cfg["random_seed"], deterministic=True, force_row_wise=True)
    ds = lgb.Dataset(X[FEATURES], y, categorical_feature=CATEGORICAL, free_raw_data=True)
    model = lgb.train(params, ds, num_boost_round=fc["lgb_num_boost_round"])

    # Recursive forecast: predictions are written back so later horizon days can use them as lags.
    Yw = np.nan_to_num(panel.Y[:, : origin - 1 + H], nan=0.0).copy()
    Yw[:, origin - 1:] = np.nan   # future actuals hidden; any read of them yields NaN and fails the assert
    out = np.zeros((panel.n, H))
    for k in range(H):
        d = origin + k
        f = features_for_day(panel, Yw, d)
        Xd = pd.DataFrame(f)[FEATURES]
        lagcols = ["lag_7", "lag_14", "lag_28", "rmean_7", "rstd_7", "rmean_28", "rstd_28"]
        assert np.isfinite(Xd[lagcols].to_numpy()).all(), "feature read beyond the forecast origin"
        pred = np.clip(model.predict(Xd), 0, None)
        out[:, k] = pred
        Yw[:, d - 1] = pred
    log = dict(model="lgbm", origin=origin, train_min_d=int(days.min()), train_max_d=int(days.max()),
               test_min_d=origin, test_max_d=origin + H - 1, n_train_rows=int(len(y)))
    return out, log


# --- evaluation ----------------------------------------------------------------------

def evaluate(panel: Panel, fc: pd.DataFrame, eval_origins) -> tuple[pd.DataFrame, pd.DataFrame]:
    ev = fc[fc["origin"].isin(eval_origins)].copy()
    ev["abs_err"] = (ev["actual"] - ev["forecast"]).abs()
    ev["err"] = ev["forecast"] - ev["actual"]
    detail = ev.groupby(["model", "origin", "series_idx"]).agg(
        sum_abs_err=("abs_err", "sum"), sum_err=("err", "sum"), sum_actual=("actual", "sum"),
        mae=("abs_err", "mean")).reset_index()
    scales = {(i, o): metrics.mase_scale(history(panel, i, o), 7)
              for i in range(panel.n) for o in eval_origins}
    detail["mase_scale"] = [scales[(i, o)] for i, o in zip(detail.series_idx, detail.origin)]
    detail["mase"] = np.where(detail["mase_scale"] > 0, detail["mae"] / detail["mase_scale"], np.nan)
    detail = detail.merge(panel.static[["series_idx", "abc", "xyz", "cell", "cat_id"]], on="series_idx")

    rows = []
    groupings = {"all": None, "abc": "abc", "xyz": "xyz", "cat": "cat_id", "cell": "cell", "fold": "origin"}
    for gtype, col in groupings.items():
        keys = ["model"] + ([col] if col else [])
        for key, g in detail.groupby(keys):
            key = key if isinstance(key, tuple) else (key,)
            sa = g["sum_actual"].sum()
            rows.append(dict(
                group_type=gtype, group_value=str(key[1]) if col else "all", model=key[0],
                wape=g["sum_abs_err"].sum() / sa if sa > 0 else np.nan,
                bias=g["sum_err"].sum() / sa if sa > 0 else np.nan,
                mase=g["mase"].mean(), n_series=g["series_idx"].nunique(), n_series_folds=len(g),
                n_mase_undefined=int(g["mase"].isna().sum())))
    return detail, pd.DataFrame(rows)


def plot_wape(acc: pd.DataFrame, out) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = acc[acc.group_type == "xyz"].pivot(index="group_value", columns="model", values="wape")[MODELS]
    allrow = acc[acc.group_type == "all"].set_index("model")["wape"][MODELS]
    d.loc["all"] = allrow
    ax = d.plot(kind="bar", figsize=(9, 4.5), width=0.8)
    ax.set_ylabel("WAPE")
    ax.set_xlabel("XYZ class")
    ax.set_title("WAPE by model and XYZ class, 4 rolling-origin folds x 28 days")
    ax.legend(title="model", ncols=5, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.14), frameon=False)
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(out, dpi=150)
    plt.close()


def main() -> None:
    cfg = load_config()
    w = windows(cfg)
    H = w.horizon
    con = connect(cfg)
    panel = load_panel(con, w.last_day)
    print(f"panel: {panel.n} series x {panel.Y.shape[1]} days")

    recs, logs = [], []

    def add(model, origin, i, f, method=None):
        recs.append(pd.DataFrame(dict(model=model, origin=origin, series_idx=i, d=np.arange(origin, origin + H),
                                      forecast=f, actual=panel.Y[i, origin - 1: origin - 1 + H],
                                      method=method or model)))

    # Baselines and ETS on evaluation folds.
    for o in w.eval_origins:
        hs = [history(panel, i, o) for i in range(panel.n)]
        for i, h in enumerate(hs):
            add("naive", o, i, fc_naive(h, H))
            add("snaive", o, i, fc_snaive(h, H))
            add("ma28", o, i, fc_ma(h, H))
        args = [(h[-cfg["forecast"]["ets_fit_window_days"]:], H, cfg["forecast"]["ets_max_zero_share"]) for h in hs]
        with ProcessPoolExecutor() as ex:
            ets = list(ex.map(fc_ets, args, chunksize=20))
        for i, (f, method) in enumerate(ets):
            add("ets", o, i, f, method)
        # history() slices columns [0, o-1), i.e. days 1..o-1; record the last day actually visible.
        max_train = panel.Y[:, : o - 1].shape[1]
        for m in ["naive", "snaive", "ma28", "ets"]:
            logs.append(dict(model=m, origin=o, train_min_d=1, train_max_d=int(max_train),
                             test_min_d=o, test_max_d=o + H - 1, n_train_rows=None))
        print(f"origin {o}: baselines + ETS done ({sum(1 for _, m in ets if m != 'ets')} ETS fallbacks)")

    # LightGBM at every origin (warm-up + simulation + evaluation).
    for o in w.lgb_origins:
        f, log = lgbm_origin(panel, o, H, cfg)
        for i in range(panel.n):
            add("lgbm", o, i, f[i])
        logs.append(log)
        print(f"origin {o}: lgbm trained on {log['n_train_rows']:,} rows, train days {log['train_min_d']}-{log['train_max_d']}")

    fc = pd.concat(recs, ignore_index=True)
    fold_log = pd.DataFrame(logs)
    assert (fold_log["train_max_d"] < fold_log["test_min_d"]).all(), "leakage: training day >= forecast day"

    detail, acc = evaluate(panel, fc, w.eval_origins)
    for name, df in [("forecasts", fc), ("fold_log", fold_log), ("forecast_eval_detail", detail),
                     ("forecast_accuracy", acc)]:
        con.register("tmp_df", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")
    con.close()

    plot_wape(acc, path(cfg, "figures_dir") / "wape_by_model_xyz.png")
    print(acc[acc.group_type == "all"][["model", "wape", "bias", "mase"]].to_string(index=False))


if __name__ == "__main__":
    main()
