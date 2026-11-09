"""Inventory policy simulation against real daily demand for the stratified sample.

Policies (continuous (s, Q) with daily review, lost sales):
  P0 naive            ROP/Q from the trailing 90-day mean and std before simulation start; fixed.
  P1 static ROP/EOQ   ROP/Q from the full post-launch training history before simulation start; fixed.
  P2 forecast-driven  ROP/Q from the LightGBM 28-day forecast mean and the std of out-of-sample LightGBM
                      errors over the prior 3 blocks; recomputed every 28 days.

Timing convention: an order placed at the end of day t with lead time L is received at the start of day
t + L + 1, so it covers exactly L days of demand exposure (days t+1 .. t+L), matching ROP = d_bar * L + SS.

Outputs: tables sim_scenarios, policy_params, policy_results, policy_summary.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

import metrics
from common import connect, load_config, windows

POLICIES = ["P0_naive", "P1_static", "P2_forecast"]


# --- core engine (pure, unit tested) ------------------------------------------------

@dataclass
class SimResult:
    on_hand_end: np.ndarray   # end-of-day on-hand units
    sales: np.ndarray
    lost: np.ndarray
    order_qty: np.ndarray     # units ordered at end of each day (0 if none)
    orders: list              # (place_day, receive_day, qty)


def simulate_series(demand, rop, q, lead_time: int, sigma_l: float, rng: np.random.Generator,
                    initial_on_hand: float) -> SimResult:
    """Daily (s, Q) simulation with lost sales.

    demand: int array, length N. rop, q: arrays of length N (per-day policy parameters).
    Lead time per order = max(1, round(lead_time + sigma_l * N(0, 1))).
    """
    demand = np.asarray(demand, float)
    rop = np.broadcast_to(np.asarray(rop, float), demand.shape)
    q = np.broadcast_to(np.asarray(q, float), demand.shape)
    N = len(demand)
    on_hand = float(initial_on_hand)
    pipeline = np.zeros(N + 1)           # receipts by day; receipts after the horizon are dropped
    on_order = 0.0
    out_oh, out_s, out_l, out_q = (np.zeros(N) for _ in range(4))
    orders = []
    for t in range(N):
        # 1. receive
        if pipeline[t]:
            on_hand += pipeline[t]
            on_order -= pipeline[t]
        # 2. demand
        s = min(demand[t], on_hand)
        on_hand -= s
        out_s[t], out_l[t] = s, demand[t] - s
        # 3. review and order
        if on_hand + on_order <= rop[t]:
            lt = max(1, int(round(lead_time + sigma_l * rng.standard_normal())))
            qty = q[t]
            arrive = t + lt + 1
            if arrive < N:
                pipeline[arrive] += qty
            on_order += qty
            out_q[t] = qty
            orders.append((t, arrive, qty))
        # 4. end-of-day position
        out_oh[t] = on_hand
    return SimResult(out_oh, out_s, out_l, out_q, orders)


def cycle_service_level(res: SimResult) -> tuple[int, int]:
    """(orders whose lead-time window had no lost sales, orders whose lead-time window is fully observed).
    Lead-time window of an order placed end of day t and received start of day r is days t+1 .. r-1."""
    N = len(res.lost)
    ok = evaluated = 0
    for t, r, _ in res.orders:
        if r > N:
            continue
        evaluated += 1
        ok += int(res.lost[t + 1: r].sum() == 0)
    return ok, evaluated


def kpis(res: SimResult, demand, price, unit_cost: float, holding_rate: float, order_cost: float,
         days_per_year: int) -> dict:
    demand = np.asarray(demand, float)
    price = np.asarray(price, float)
    ok, ev = cycle_service_level(res)
    n_orders = int((res.order_qty > 0).sum())
    holding = float(res.on_hand_end.sum() * unit_cost * holding_rate / days_per_year)
    ordering = n_orders * order_cost
    lost_value = float((res.lost * price).sum())
    lost_margin = float((res.lost * (price - unit_cost)).sum())
    return dict(
        units_demanded=float(demand.sum()), units_sold=float(res.sales.sum()), units_lost=float(res.lost.sum()),
        fill_rate=float(res.sales.sum() / demand.sum()) if demand.sum() > 0 else np.nan,
        cycles_ok=ok, cycles_evaluated=ev, cycle_service_level=ok / ev if ev else np.nan,
        stockout_days=int((res.lost > 0).sum()), n_orders=n_orders,
        avg_on_hand=float(res.on_hand_end.mean()), avg_inventory_value=float(res.on_hand_end.mean() * unit_cost),
        holding_cost=holding, order_cost=ordering, lost_sales_value=lost_value, lost_margin=lost_margin,
        total_cost=holding + ordering + lost_margin, revenue=float((res.sales * price).sum()),
        min_on_hand=float(res.on_hand_end.min()),
    )


def policy_params(d_bar: float, sigma_d: float, z: float, lead_time: float, sigma_l: float,
                  unit_cost: float, holding_rate: float, order_cost: float, days_per_year: int) -> tuple[float, float]:
    """(ROP, Q). Q = EOQ rounded up, at least 1 unit."""
    ss = metrics.safety_stock(z, lead_time, sigma_d, d_bar, sigma_l)
    rop = metrics.reorder_point(d_bar, lead_time, ss)
    e = metrics.eoq(d_bar * days_per_year, order_cost, holding_rate * unit_cost)
    return rop, float(max(1, np.ceil(e)))


# --- inputs -------------------------------------------------------------------------

def load_inputs(con, cfg, w):
    sample = con.execute("SELECT series_idx, item_id, store_id, cat_id, abc, xyz, cell, first_sale_d "
                         "FROM sample_series ORDER BY series_idx").df()
    n = len(sample)
    long = con.execute(
        """SELECT s.series_idx, f.d, f.units, f.sell_price
           FROM fact_sales f JOIN sample_series s USING (item_id, store_id)"""
    ).df()
    T = int(long.d.max())
    Y = np.full((n, T), np.nan)
    P = np.full((n, T), np.nan)
    Y[long.series_idx, long.d - 1] = long.units
    P[long.series_idx, long.d - 1] = long.sell_price
    P = pd.DataFrame(P).T.ffill().bfill().T.to_numpy()
    first = sample.first_sale_d.to_numpy().astype(int)
    for i in range(n):
        Y[i, : first[i] - 1] = np.nan

    lg = con.execute("SELECT origin, series_idx, d, forecast, actual FROM forecasts WHERE model = 'lgbm'").df()
    F = {o: g.pivot(index="series_idx", columns="d", values="forecast").sort_index().to_numpy()
         for o, g in lg.groupby("origin")}
    E = {o: (g.pivot(index="series_idx", columns="d", values="actual")
             - g.pivot(index="series_idx", columns="d", values="forecast")).sort_index().to_numpy()
         for o, g in lg.groupby("origin")}
    return sample, Y, P, F, E


def build_params(sample, Y, P, F, E, cfg, w, z_by_series, L, sigma_l):
    """Per-series daily ROP and Q arrays for the simulation window, for each policy."""
    inv = cfg["inventory"]
    N = w.sim_end - w.sim_start + 1
    s0 = w.sim_start - 1                       # column index of first simulated day
    hr, S, dpy = inv["annual_holding_rate"], inv["order_cost"], inv["days_per_year"]
    nw, ew = inv["naive_window_days"], inv["p2_error_window_blocks"]
    sim_origins = [o for o in w.lgb_origins if o >= w.sim_start]
    rows = []
    out = {p: (np.zeros((len(sample), N)), np.zeros((len(sample), N))) for p in POLICIES}
    unit_cost = np.zeros(len(sample))
    for i in range(len(sample)):
        hist = Y[i, :s0]
        hist = hist[~np.isnan(hist)]
        price_hist = P[i, s0 - cfg["calendar"]["segment_window_days"]: s0]
        unit_cost[i] = inv["unit_cost_share_of_price"] * price_hist.mean()
        uc, z = unit_cost[i], z_by_series[i]

        for pol, (d_bar, sd) in {"P0_naive": (hist[-nw:].mean(), hist[-nw:].std()),
                                 "P1_static": (hist.mean(), hist.std())}.items():
            rop, q = policy_params(d_bar, sd, z, L, sigma_l, uc, hr, S, dpy)
            out[pol][0][i, :], out[pol][1][i, :] = rop, q
            rows.append(dict(series_idx=i, policy=pol, block_start_d=w.sim_start, d_bar=d_bar, sigma_d=sd, rop=rop, q=q))

        for b, o in enumerate(sim_origins):
            d_bar = float(F[o][i].mean())
            prior = [E[po][i] for po in w.lgb_origins if o - ew * w.horizon <= po < o]
            sd = float(np.concatenate(prior).std())
            rop, q = policy_params(d_bar, sd, z, L, sigma_l, uc, hr, S, dpy)
            sl = slice(o - w.sim_start, o - w.sim_start + w.horizon)
            out["P2_forecast"][0][i, sl], out["P2_forecast"][1][i, sl] = rop, q
            rows.append(dict(series_idx=i, policy="P2_forecast", block_start_d=o, d_bar=d_bar, sigma_d=sd, rop=rop, q=q))
    return out, unit_cost, pd.DataFrame(rows)


# --- driver -------------------------------------------------------------------------

def scenarios(cfg) -> pd.DataFrame:
    inv, sc = cfg["inventory"], cfg["scenarios"]
    rows = [dict(scenario="base", kind="base", service_level=None, lead_time=inv["lead_time_days"],
                 lead_time_sd=inv["lead_time_sd_days"])]
    for sl in sc["service_level_sweep"]:
        rows.append(dict(scenario=f"sl_{sl}", kind="service_level", service_level=sl,
                         lead_time=inv["lead_time_days"], lead_time_sd=inv["lead_time_sd_days"]))
    for L in sc["lead_times"]:
        for sd in sc["lead_time_sds"]:
            if L == inv["lead_time_days"] and sd == inv["lead_time_sd_days"]:
                continue  # identical to base
            rows.append(dict(scenario=f"lt_{L}_sd_{sd}", kind="lead_time", service_level=None, lead_time=L, lead_time_sd=sd))
    return pd.DataFrame(rows)


def run_scenario(args):
    scen, sample, Y, P, F, E, cfg, w = args
    inv = cfg["inventory"]
    if pd.isna(scen["service_level"]):   # base and lead-time scenarios use per-cell targets
        z_by_series = np.array([metrics.z_value(cfg["service_level_targets"][c]) for c in sample.cell])
    else:
        z_by_series = np.full(len(sample), metrics.z_value(scen["service_level"]))
    L, sl = int(scen["lead_time"]), float(scen["lead_time_sd"])
    params, unit_cost, ptab = build_params(sample, Y, P, F, E, cfg, w, z_by_series, L, sl)
    demand = Y[:, w.sim_start - 1: w.sim_end]
    price = P[:, w.sim_start - 1: w.sim_end]
    results = []
    for pol in POLICIES:
        rop, q = params[pol]
        for i in range(len(sample)):
            rng = np.random.default_rng([cfg["random_seed"], i])   # same lead-time draw stream for every policy
            res = simulate_series(demand[i], rop[i], q[i], L, sl, rng, np.ceil(rop[i, 0]))
            k = kpis(res, demand[i], price[i], unit_cost[i], inv["annual_holding_rate"], inv["order_cost"],
                     inv["days_per_year"])
            results.append(dict(scenario=scen["scenario"], policy=pol, series_idx=i, unit_cost=unit_cost[i], **k))
    ptab.insert(0, "scenario", scen["scenario"])
    return pd.DataFrame(results), ptab


def summarize(res: pd.DataFrame) -> pd.DataFrame:
    g = res.groupby(["scenario", "policy"])
    s = g.agg(units_demanded=("units_demanded", "sum"), units_sold=("units_sold", "sum"),
              units_lost=("units_lost", "sum"), cycles_ok=("cycles_ok", "sum"),
              cycles_evaluated=("cycles_evaluated", "sum"), stockout_days=("stockout_days", "sum"),
              n_orders=("n_orders", "sum"), avg_inventory_value=("avg_inventory_value", "sum"),
              holding_cost=("holding_cost", "sum"), order_cost=("order_cost", "sum"),
              lost_sales_value=("lost_sales_value", "sum"), lost_margin=("lost_margin", "sum"),
              total_cost=("total_cost", "sum"), revenue=("revenue", "sum"), n_series=("series_idx", "nunique")
              ).reset_index()
    s["fill_rate"] = s.units_sold / s.units_demanded
    s["cycle_service_level"] = s.cycles_ok / s.cycles_evaluated
    s["cost_per_unit_sold"] = s.total_cost / s.units_sold
    naive = s[s.policy == "P0_naive"].set_index("scenario")["total_cost"]
    s["cost_delta_vs_naive"] = s.total_cost / s.scenario.map(naive) - 1
    return s


def main() -> None:
    cfg = load_config()
    w = windows(cfg)
    con = connect(cfg)
    sample, Y, P, F, E = load_inputs(con, cfg, w)
    sc = scenarios(cfg)
    jobs = [(row._asdict(), sample, Y, P, F, E, cfg, w) for row in sc.itertuples(index=False)]
    with ProcessPoolExecutor() as ex:
        outs = list(ex.map(run_scenario, jobs))
    res = pd.concat([o[0] for o in outs], ignore_index=True)
    ptab = pd.concat([o[1] for o in outs], ignore_index=True)
    res = res.merge(sample[["series_idx", "item_id", "store_id", "cat_id", "abc", "xyz", "cell"]], on="series_idx")

    assert res.fill_rate.dropna().between(0, 1).all(), "fill rate outside [0, 1]"
    assert (res.min_on_hand >= 0).all(), "negative inventory"
    summ = summarize(res)
    for name, df in [("sim_scenarios", sc), ("policy_params", ptab), ("policy_results", res), ("policy_summary", summ)]:
        con.register("tmp_df", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")
    con.close()
    cols = ["scenario", "policy", "fill_rate", "cycle_service_level", "avg_inventory_value", "total_cost",
            "cost_delta_vs_naive"]
    print(summ[summ.scenario == "base"][cols].to_string(index=False))


if __name__ == "__main__":
    main()
