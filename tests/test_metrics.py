"""Metrics checked against hand-computed values."""
import math

import pytest

import metrics

ACTUAL = [0, 2, 4, 0, 4]
FORECAST = [1, 1, 3, 1, 4]
TRAIN = [1, 2, 3, 4, 5, 6, 7, 3, 2, 3]


def test_wape():
    # |errors| = 1, 1, 1, 1, 0 -> 4; sum(actual) = 10
    assert metrics.wape(ACTUAL, FORECAST) == pytest.approx(0.4)


def test_wape_zero_actuals_is_nan():
    assert math.isnan(metrics.wape([0, 0], [1, 1]))


def test_bias():
    # forecast - actual = 1, -1, -1, 1, 0 -> 0
    assert metrics.bias(ACTUAL, FORECAST) == pytest.approx(0.0)
    # over-forecast by 2 units on 10 actual -> +0.2
    assert metrics.bias(ACTUAL, [1, 2, 4, 0, 5]) == pytest.approx(0.2)


def test_mase():
    # seasonal naive (m=7) in-sample errors: |3-1|, |2-2|, |3-3| -> mean 2/3
    assert metrics.mase_scale(TRAIN, 7) == pytest.approx(2 / 3)
    # MAE = 4/5 = 0.8 -> MASE = 0.8 / (2/3) = 1.2
    assert metrics.mase(ACTUAL, FORECAST, TRAIN, 7) == pytest.approx(1.2)


def test_mase_zero_scale_is_nan():
    assert math.isnan(metrics.mase([1], [1], [0] * 14, 7))


def test_z_value():
    assert metrics.z_value(0.95) == pytest.approx(1.6448536, abs=1e-6)
    assert metrics.z_value(0.5) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        metrics.z_value(1.0)


def test_safety_stock_and_rop():
    # z=2, L=4, sigma_d=2, d_bar=10, sigma_L=1 -> 2 * sqrt(4*4 + 100*1) = 2 * sqrt(116)
    ss = metrics.safety_stock(2.0, 4, 2.0, 10.0, 1.0)
    assert ss == pytest.approx(2 * math.sqrt(116))
    assert metrics.reorder_point(10.0, 4, ss) == pytest.approx(40 + 2 * math.sqrt(116))
    # no lead time variability -> z * sigma_d * sqrt(L) = 2 * 2 * 2
    assert metrics.safety_stock(2.0, 4, 2.0, 10.0, 0.0) == pytest.approx(8.0)


def test_eoq():
    # sqrt(2 * 200 * 25 / 1) = 100
    assert metrics.eoq(200, 25, 1) == pytest.approx(100.0)
    assert metrics.eoq(0, 25, 1) == 0.0
