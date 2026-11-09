"""Build marts (sql/03_marts.sql), export them to CSV for Power BI, and draw the report figures."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np

from common import ROOT, connect, load_config, path

MARTS = ["mart_kpi", "mart_abc_xyz", "mart_forecast_accuracy", "mart_forecast_summary", "mart_policy_results",
         "mart_service_cost_curve", "dim_calendar", "dim_item", "dim_store"]


def fig_matrix(con, out):
    m = con.execute("SELECT abc, xyz, series, revenue_share FROM mart_abc_xyz").df()
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8))
    for ax, col, title, fmt in [(axes[0], "series", "Series count", "{:,.0f}"),
                                (axes[1], "revenue_share", "Revenue share", "{:.1%}")]:
        grid = m.pivot(index="abc", columns="xyz", values=col).reindex(index=list("ABC"), columns=list("XYZ")).fillna(0)
        vals = grid.to_numpy()
        ax.imshow(vals, cmap="Blues")
        for (r, c), v in np.ndenumerate(vals):
            ax.text(c, r, fmt.format(v), ha="center", va="center", fontsize=9,
                    color="white" if v > 0.6 * vals.max() else "black")
        ax.set_xticks(range(3), list("XYZ"))
        ax.set_yticks(range(3), list("ABC"))
        ax.set_title(title)
    fig.suptitle("ABC-XYZ matrix, all series, 52 weeks before simulation")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def fig_curve(con, out):
    c = con.execute("SELECT * FROM mart_service_cost_curve").df()
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for pol, g in c.groupby("policy"):
        axes[0].plot(g.target_service_level, g.total_cost, marker="o", label=pol)
        axes[1].plot(g.fill_rate, g.total_cost, marker="o", label=pol)
    axes[0].set_xlabel("target cycle service level")
    axes[1].set_xlabel("achieved fill rate")
    for ax in axes:
        ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
        ax.set_ylabel("total cost, 364 days, 1,000 series ($)")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
    fig.suptitle("Service level vs total cost (holding + ordering + lost margin), L = 7, sigma_L = 1")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def md_table(df) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(out)


def results_md(con, out) -> None:
    """Headline numbers for README and case study. Generated; do not edit by hand."""
    q = lambda s: con.execute(s).df()
    kpi = q("""SELECT policy, round(fill_rate * 100, 2) AS fill_rate_pct, round(cycle_service_level * 100, 1) AS csl_pct,
                      round(avg_inventory_value) AS avg_inventory_value, round(holding_cost) AS holding,
                      round(order_cost) AS ordering, round(lost_margin) AS lost_margin, round(total_cost) AS total_cost,
                      round(cost_delta_vs_naive * 100, 1) AS cost_vs_p0_pct
               FROM mart_kpi ORDER BY policy""")
    p2_vs_p1 = q("""SELECT round((max(total_cost) FILTER (WHERE policy = 'P2_forecast')
                     / max(total_cost) FILTER (WHERE policy = 'P1_static') - 1) * 100, 1) AS v FROM mart_kpi""").v[0]
    acc = q("""SELECT model, round(wape, 3) AS wape, round(bias, 3) AS bias, round(mase, 3) AS mase
               FROM mart_forecast_summary WHERE group_type = 'all' ORDER BY wape""")
    acc_xyz = q("""SELECT group_type AS by_, group_value AS class, model, round(wape, 3) AS wape
                   FROM mart_forecast_summary WHERE group_type IN ('abc', 'xyz')""").pivot(
        index=["by_", "class"], columns="model", values="wape").reset_index()
    ets_fb = q("""SELECT count(DISTINCT (series_idx, origin)) FILTER (WHERE method <> 'ets') AS fallback,
                         count(DISTINCT (series_idx, origin)) AS total FROM forecasts WHERE model = 'ets'""")
    curve = q("""SELECT policy, target_service_level, round(fill_rate * 100, 2) AS fill_rate_pct,
                        round(cycle_service_level * 100, 1) AS csl_pct, round(total_cost) AS total_cost
                 FROM mart_service_cost_curve ORDER BY policy, target_service_level""")
    lt = q("""SELECT p.policy, s.lead_time, s.lead_time_sd, round(p.fill_rate * 100, 2) AS fill_rate_pct,
                     round(p.total_cost) AS total_cost, round(p.cost_delta_vs_naive * 100, 1) AS cost_vs_p0_pct
              FROM policy_summary p JOIN sim_scenarios s USING (scenario)
              WHERE s.kind IN ('base', 'lead_time') ORDER BY s.lead_time, s.lead_time_sd, p.policy""")
    cell = q("""SELECT cell, policy, round(sum(units_sold) / sum(units_demanded) * 100, 2) AS fill_rate_pct,
                       round(sum(cycles_ok) / sum(cycles_evaluated) * 100, 1) AS csl_pct, round(sum(total_cost)) AS total_cost
                FROM mart_policy_results WHERE scenario = 'base' GROUP BY cell, policy ORDER BY cell, policy""")
    cell["cost_above_cheapest"] = cell["total_cost"] - cell.groupby("cell")["total_cost"].transform("min")
    best = cell.loc[cell.groupby("cell")["total_cost"].idxmin(), ["cell", "policy", "total_cost"]]
    abc = q("""SELECT abc, sum(series)::INTEGER AS series, round(sum(revenue_share) * 100, 1) AS revenue_share_pct,
                      round(sum(series_share) * 100, 1) AS series_share_pct
               FROM mart_abc_xyz GROUP BY abc ORDER BY abc""")
    text = [
        "# Results", "", "Generated by `src/report.py` from the marts. Do not edit by hand.", "",
        "## ABC classes, all 30,490 series", "", md_table(abc), "",
        "## Policy comparison, base scenario (cell targets, L = 7, sigma_L = 1, 1,000 series, 364 days)", "",
        md_table(kpi), "", f"P2 total cost vs P1: {p2_vs_p1}%", "",
        "## Forecast accuracy, 4 folds x 28 days, 1,000 series", "", md_table(acc), "",
        md_table(acc_xyz), "",
        f"ETS fell back to the 28-day moving average on {int(ets_fb.fallback[0]):,} of {int(ets_fb.total[0]):,} series-folds.", "",
        "## Service level sweep (uniform target, L = 7, sigma_L = 1)", "", md_table(curve), "",
        "## Lead time scenarios (cell targets)", "", md_table(lt), "",
        "## Base scenario by ABC-XYZ cell", "", md_table(cell), "",
        "Lowest total cost per cell:", "", md_table(best), "",
    ]
    out.write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    cfg = load_config()
    con = connect(cfg)
    con.execute((ROOT / "sql" / "03_marts.sql").read_text(encoding="utf-8"))
    out_dir = path(cfg, "marts_dir")
    for t in MARTS:
        f = out_dir / f"{t}.csv"
        con.execute(f"COPY (SELECT * FROM {t}) TO '{f.as_posix()}' (HEADER, DELIMITER ',')")
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"{t}: {n:,} rows -> {f.relative_to(ROOT)}")
    figs = path(cfg, "figures_dir")
    fig_matrix(con, figs / "abc_xyz_matrix.png")
    fig_curve(con, figs / "service_cost_curve.png")
    results_md(con, ROOT / "docs" / "results.md")
    con.close()


if __name__ == "__main__":
    main()
