"""ABC-XYZ segmentation on the full dataset and the stratified forecasting/simulation sample.

Python computes the segmentation independently from daily rows and must agree with sql/02_abc_xyz.sql.
Outputs: tables seg_abc_xyz, seg_matrix, sample_series; file data/processed/sample_series.csv.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import ROOT, connect, load_config, path, run_sql_file, windows


def classify(df: pd.DataFrame, abc_cut: dict, xyz_cut: dict) -> pd.DataFrame:
    """df: one row per series with revenue, mean_weekly_units, sd_weekly_units, item_id, store_id."""
    out = df.sort_values(["revenue", "item_id", "store_id"], ascending=[False, True, True]).copy()
    out["cum_revenue_share"] = out["revenue"].cumsum() / out["revenue"].sum()
    pos = out["revenue"] > 0
    out["abc"] = np.select(
        [pos & (out["cum_revenue_share"] <= abc_cut["A"]), pos & (out["cum_revenue_share"] <= abc_cut["B"])],
        ["A", "B"], default="C",
    )
    mean = out["mean_weekly_units"]
    # rounded to 10 decimals, same as SQL, so threshold ties classify identically
    out["cv"] = np.where(mean > 0, (out["sd_weekly_units"] / mean.where(mean > 0)).round(10), np.nan)
    out["xyz"] = np.select(
        [mean == 0, out["cv"] < xyz_cut["X"], out["cv"] <= xyz_cut["Y"]], ["Z", "X", "Y"], default="Z"
    )
    return out


def python_segmentation(con, w, abc_cut, xyz_cut) -> pd.DataFrame:
    daily = con.execute(
        f"""SELECT item_id, store_id, d, units, coalesce(sell_price, 0) AS price
            FROM v_sales_clean WHERE d BETWEEN {w.seg_start} AND {w.seg_end}"""
    ).df()
    daily["rev"] = daily["units"] * daily["price"]
    daily["wk"] = (daily["d"] - w.seg_start) // 7
    agg = daily.groupby(["item_id", "store_id"]).agg(revenue=("rev", "sum"), units=("units", "sum")).reset_index()
    weekly = daily.groupby(["item_id", "store_id", "wk"])["units"].sum().reset_index()
    stats = weekly.groupby(["item_id", "store_id"])["units"].agg(
        mean_weekly_units="mean", sd_weekly_units=lambda s: s.std(ddof=0)
    ).reset_index()
    series = con.execute("SELECT item_id, store_id, cat_id, dept_id, state_id, first_sale_d FROM dim_series").df()
    df = series.merge(agg, how="left", on=["item_id", "store_id"]).merge(stats, how="left", on=["item_id", "store_id"])
    df[["revenue", "units", "mean_weekly_units"]] = df[["revenue", "units", "mean_weekly_units"]].fillna(0)
    return classify(df, abc_cut, xyz_cut)


def stratified_sample(seg: pd.DataFrame, n: int, seed: int, seg_start: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Proportional allocation over (abc, xyz, cat_id) strata, largest-remainder rounding,
    at least one series per non-empty stratum. Eligible: launched on or before the segmentation
    window start and sold at least one unit in the window."""
    elig = seg[(seg["first_sale_d"] <= seg_start) & (seg["units"] > 0)].copy()
    elig = elig.sort_values(["abc", "xyz", "cat_id", "store_id", "item_id"]).reset_index(drop=True)
    strata = elig.groupby(["abc", "xyz", "cat_id"]).size().rename("N").reset_index()
    raw = n * strata["N"] / strata["N"].sum()
    alloc = np.maximum(np.floor(raw).astype(int), 1)
    remainder = n - alloc.sum()
    order = np.argsort(-(raw - np.floor(raw)).to_numpy(), kind="stable")
    i = 0
    while remainder > 0:
        k = order[i % len(order)]
        if alloc[k] < strata.loc[k, "N"]:
            alloc[k] += 1
            remainder -= 1
        i += 1
    while remainder < 0:  # only if minimum-1 rule over-allocated: trim largest strata
        k = int(np.argmax(alloc))
        alloc[k] -= 1
        remainder += 1
    strata["n"] = np.minimum(alloc, strata["N"])

    rng = np.random.default_rng(seed)
    parts = []
    for row in strata.itertuples():
        g = elig[(elig["abc"] == row.abc) & (elig["xyz"] == row.xyz) & (elig["cat_id"] == row.cat_id)]
        idx = rng.choice(len(g), size=row.n, replace=False)
        parts.append(g.iloc[np.sort(idx)])
    sample = pd.concat(parts).sort_values(["store_id", "item_id"]).reset_index(drop=True)
    sample["series_idx"] = np.arange(len(sample))
    return sample, strata


def main() -> None:
    cfg = load_config()
    w = windows(cfg)
    s = cfg["segmentation"]
    con = connect(cfg)
    params = dict(seg_start=w.seg_start, seg_end=w.seg_end, abc_a=s["abc_cutoffs"]["A"], abc_b=s["abc_cutoffs"]["B"],
                  xyz_x=s["xyz_cutoffs"]["X"], xyz_y=s["xyz_cutoffs"]["Y"])
    run_sql_file(con, ROOT / "sql" / "02_abc_xyz.sql", params)
    sql_seg = con.execute("SELECT * FROM seg_abc_xyz_sql").df()

    py_seg = python_segmentation(con, w, s["abc_cutoffs"], s["xyz_cutoffs"])
    cmp = py_seg.merge(sql_seg, on=["item_id", "store_id"], suffixes=("_py", "_sql"))
    assert len(cmp) == len(py_seg) == len(sql_seg), "series count mismatch between Python and SQL"
    abc_mis = int((cmp["abc_py"] != cmp["abc_sql"]).sum())
    xyz_mis = int((cmp["xyz_py"] != cmp["xyz_sql"]).sum())
    rev_diff = float((cmp["revenue_py"] - cmp["revenue_sql"]).abs().max())
    print(f"Python vs SQL: abc mismatches={abc_mis}, xyz mismatches={xyz_mis}, max revenue diff={rev_diff:.6f}")
    assert abc_mis == 0 and xyz_mis == 0 and rev_diff < 1e-4, "Python and SQL segmentation disagree"

    seg = py_seg.copy()
    seg["cell"] = seg["abc"] + seg["xyz"]
    seg["target_service_level"] = seg["cell"].map(cfg["service_level_targets"])
    con.register("seg_df", seg)
    con.execute("CREATE OR REPLACE TABLE seg_abc_xyz AS SELECT * FROM seg_df ORDER BY store_id, item_id")

    matrix = seg.groupby(["abc", "xyz"]).agg(series=("item_id", "size"), revenue=("revenue", "sum")).reset_index()
    matrix["revenue_share"] = matrix["revenue"] / matrix["revenue"].sum()
    matrix["series_share"] = matrix["series"] / matrix["series"].sum()
    con.register("matrix_df", matrix)
    con.execute("CREATE OR REPLACE TABLE seg_matrix AS SELECT * FROM matrix_df ORDER BY abc, xyz")
    empty = [f"{a}{x}" for a in "ABC" for x in "XYZ"
             if matrix[(matrix.abc == a) & (matrix.xyz == x)]["series"].sum() == 0]
    if empty:
        print(f"WARNING: empty ABC-XYZ cells: {empty}. Record a threshold decision in docs/decisions.md.")

    sample, strata = stratified_sample(seg, s["sample_size"], cfg["random_seed"], w.seg_start)
    con.register("sample_df", sample)
    con.execute("CREATE OR REPLACE TABLE sample_series AS SELECT * FROM sample_df ORDER BY series_idx")
    con.register("strata_df", strata)
    con.execute("CREATE OR REPLACE TABLE sample_strata AS SELECT * FROM strata_df")
    out = path(cfg, "processed_dir") / "sample_series.csv"
    sample[["series_idx", "item_id", "store_id", "cat_id", "dept_id", "state_id", "abc", "xyz", "cell"]].to_csv(out, index=False)
    con.close()

    print(matrix.to_string(index=False))
    print(f"sample: {len(sample)} series across {int((strata['n'] > 0).sum())} strata -> {out}")


if __name__ == "__main__":
    main()
