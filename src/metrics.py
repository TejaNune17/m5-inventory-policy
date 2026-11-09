"""Forecast accuracy metrics and inventory policy formulas. Pure functions, unit tested."""
from __future__ import annotations

import numpy as np
from scipy.stats import norm


# --- forecast accuracy -------------------------------------------------------

def wape(actual, forecast) -> float:
    """sum|actual - forecast| / sum(actual). NaN if sum(actual) == 0."""
    a, f = np.asarray(actual, float), np.asarray(forecast, float)
    denom = a.sum()
    return float(np.abs(a - f).sum() / denom) if denom > 0 else float("nan")


def bias(actual, forecast) -> float:
    """sum(forecast - actual) / sum(actual). Positive = over-forecast. NaN if sum(actual) == 0."""
    a, f = np.asarray(actual, float), np.asarray(forecast, float)
    denom = a.sum()
    return float((f - a).sum() / denom) if denom > 0 else float("nan")


def mase_scale(train, m: int = 7) -> float:
    """In-sample mean absolute error of the seasonal naive forecast with period m."""
    y = np.asarray(train, float)
    if len(y) <= m:
        return float("nan")
    return float(np.mean(np.abs(y[m:] - y[:-m])))


def mase(actual, forecast, train, m: int = 7) -> float:
    """Mean absolute error of the forecast divided by mase_scale(train, m). NaN if scale is 0."""
    scale = mase_scale(train, m)
    if not np.isfinite(scale) or scale == 0:
        return float("nan")
    a, f = np.asarray(actual, float), np.asarray(forecast, float)
    return float(np.mean(np.abs(a - f)) / scale)


# --- inventory policy --------------------------------------------------------

def z_value(service_level: float) -> float:
    """Standard normal quantile for a target cycle service level."""
    if not 0 < service_level < 1:
        raise ValueError("service level must be in (0, 1)")
    return float(norm.ppf(service_level))


def safety_stock(z: float, lead_time: float, sigma_d: float, d_bar: float, sigma_l: float) -> float:
    """z * sqrt(L * sigma_d^2 + d_bar^2 * sigma_L^2)."""
    return float(z * np.sqrt(lead_time * sigma_d ** 2 + d_bar ** 2 * sigma_l ** 2))


def reorder_point(d_bar: float, lead_time: float, ss: float) -> float:
    """d_bar * L + safety stock."""
    return float(d_bar * lead_time + ss)


def eoq(annual_demand: float, order_cost: float, annual_holding_cost_per_unit: float) -> float:
    """sqrt(2 D S / H). Returns 0 when there is no demand; inf when holding cost is 0."""
    if annual_demand <= 0:
        return 0.0
    if annual_holding_cost_per_unit <= 0:
        return float("inf")
    return float(np.sqrt(2 * annual_demand * order_cost / annual_holding_cost_per_unit))
