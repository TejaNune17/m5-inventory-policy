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
    con.close()


if __name__ == "__main__":
    main()
