"""Leakage checks for the LightGBM pipeline on a synthetic panel."""
import numpy as np
import pandas as pd

import forecast
from common import load_config, windows


def synthetic_panel(n=6, T=200, seed=0):
    rng = np.random.default_rng(seed)
    Y = rng.poisson(2, (n, T)).astype(float)
    Y[0, :30] = np.nan                       # series 0 launches on day 31
    static = pd.DataFrame(dict(series_idx=range(n), first_sale_d=[31] + [1] * (n - 1),
                               item_code=range(n), store_code=0, dept_code=0, cat_code=0, state_code=0,
                               abc="A", xyz="X", cell="AX", cat_id="FOODS"))
    return forecast.Panel(Y=Y, price=np.full((n, T), 2.0), snap=np.zeros((n, T)), dow=(np.arange(T) % 7) + 1,
                          month=np.ones(T, int), is_event=np.zeros(T, int), static=static)


def test_training_rows_strictly_before_origin():
    p = synthetic_panel()
    X, y, days = forecast.training_frame(p, origin=150, window_days=100)
    assert days.max() < 150
    assert days.min() >= 50
    assert len(X) == len(y) == len(days)


def test_training_excludes_prelaunch_lag_window():
    p = synthetic_panel()
    X, y, days = forecast.training_frame(p, origin=150, window_days=200)
    # series 0 launches day 31; its rows need 28 days of post-launch history -> first row day 59
    s0 = days[X["item_code"].to_numpy() == 0]
    assert s0.min() == 59


def test_training_frame_ignores_future_values():
    p = synthetic_panel()
    X1, y1, _ = forecast.training_frame(p, origin=150, window_days=100)
    p.Y[:, 149:] = 1e6                       # change everything on or after the origin
    X2, y2, _ = forecast.training_frame(p, origin=150, window_days=100)
    pd.testing.assert_frame_equal(X1, X2)
    assert np.array_equal(y1, y2)


def test_lgbm_forecast_ignores_future_values():
    cfg = load_config()
    cfg["forecast"]["lgb_num_boost_round"] = 10
    cfg["forecast"]["lgb_params"]["min_data_in_leaf"] = 5
    p = synthetic_panel()
    f1, log = forecast.lgbm_origin(p, 150, 28, cfg)
    assert log["train_max_d"] < log["test_min_d"]
    p.Y[:, 149:] = 1e6
    f2, _ = forecast.lgbm_origin(p, 150, 28, cfg)
    assert np.allclose(f1, f2)


def test_windows_layout():
    w = windows(load_config())
    assert w.eval_origins[-1] + w.horizon - 1 == w.last_day
    assert len(w.eval_origins) == 4
    assert w.seg_end < w.sim_start == w.lgb_origins[load_config()["calendar"]["warmup_blocks"]]
