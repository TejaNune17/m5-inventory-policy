"""Simulation engine checked against a hand-traced 10-day example.

demand = [2,3,1,0,4,2,2,3,1,2], ROP = 4, Q = 6, L = 2, sigma_L = 0, initial on-hand = 4.
An order placed end of day t arrives start of day t + L + 1.

day  recv  demand  sold  lost  on_hand_end  position  order (arrives)
0    -     2       2     0     2            2 <= 4    6 (day 3)
1    -     3       2     1     0            6
2    -     1       0     1     0            6
3    6     0       0     0     6            6
4    -     4       4     0     2            2 <= 4    6 (day 7)
5    -     2       2     0     0            6
6    -     2       0     2     0            6
7    6     3       3     0     3            3 <= 4    6 (day 10, outside horizon)
8    -     1       1     0     2            8
9    -     2       2     0     0            6
"""
import numpy as np
import pytest

from simulate import cycle_service_level, kpis, policy_params, simulate_series

DEMAND = np.array([2, 3, 1, 0, 4, 2, 2, 3, 1, 2])


def run(seed=0):
    return simulate_series(DEMAND, rop=4, q=6, lead_time=2, sigma_l=0.0,
                           rng=np.random.default_rng(seed), initial_on_hand=4)


def test_trace():
    r = run()
    assert r.sales.tolist() == [2, 2, 0, 0, 4, 2, 0, 3, 1, 2]
    assert r.lost.tolist() == [0, 1, 1, 0, 0, 0, 2, 0, 0, 0]
    assert r.on_hand_end.tolist() == [2, 0, 0, 6, 2, 0, 0, 3, 2, 0]
    assert [(t, a) for t, a, _ in r.orders] == [(0, 3), (4, 7), (7, 10)]


def test_cycle_service_level():
    # order 1 window days 1-2: lost 2 -> fail; order 2 window days 5-6: lost 2 -> fail; order 3 days 8-9: ok
    assert cycle_service_level(run()) == (1, 3)


def test_kpis():
    k = kpis(run(), DEMAND, price=np.full(10, 10.0), unit_cost=7.0, holding_rate=0.25, order_cost=20.0,
             days_per_year=365)
    assert k["fill_rate"] == pytest.approx(16 / 20)
    assert k["stockout_days"] == 3
    assert k["n_orders"] == 3
    assert k["avg_on_hand"] == pytest.approx(15 / 10)
    assert k["holding_cost"] == pytest.approx(15 * 7 * 0.25 / 365)
    assert k["order_cost"] == pytest.approx(60.0)
    assert k["lost_sales_value"] == pytest.approx(40.0)
    assert k["lost_margin"] == pytest.approx(12.0)
    assert k["total_cost"] == pytest.approx(15 * 7 * 0.25 / 365 + 60 + 12)
    assert 0 <= k["fill_rate"] <= 1


def test_inventory_never_negative_random_demand():
    rng = np.random.default_rng(1)
    d = rng.poisson(3, 365)
    r = simulate_series(d, rop=10, q=15, lead_time=5, sigma_l=2.0, rng=np.random.default_rng(2), initial_on_hand=10)
    assert (r.on_hand_end >= 0).all()
    assert np.allclose(r.sales + r.lost, d)


def test_reproducible_with_seed():
    rng = np.random.default_rng(1)
    d = rng.poisson(3, 200)
    a = simulate_series(d, 10, 15, 5, 2.0, np.random.default_rng(7), 10)
    b = simulate_series(d, 10, 15, 5, 2.0, np.random.default_rng(7), 10)
    assert np.array_equal(a.on_hand_end, b.on_hand_end) and a.orders == b.orders


def test_policy_params():
    # d_bar=10, sigma_d=2, z=2, L=4, sigma_L=0 -> SS=8, ROP=48; EOQ: D=3650, S=20, H=0.25*8=2 -> sqrt(73000)
    rop, q = policy_params(10, 2, 2.0, 4, 0.0, unit_cost=8.0, holding_rate=0.25, order_cost=20.0, days_per_year=365)
    assert rop == pytest.approx(48.0)
    assert q == np.ceil(np.sqrt(2 * 3650 * 20 / 2))
