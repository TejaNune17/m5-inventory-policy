"""Shared config loading, derived day windows, and warehouse connection."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "assumptions.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def path(cfg: dict, key: str) -> Path:
    p = ROOT / cfg["paths"][key]
    if key != "warehouse":
        p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass(frozen=True)
class Windows:
    """All day indices are M5 'd' integers (d_1 = 2011-01-29)."""
    last_day: int
    horizon: int
    sim_start: int            # first simulated day
    sim_end: int              # last simulated day (= last_day)
    seg_start: int            # first day of segmentation window
    seg_end: int              # last day of segmentation window (= sim_start - 1)
    lgb_origins: tuple[int, ...]   # first forecast day of each LightGBM origin, warm-up included
    eval_origins: tuple[int, ...]  # first forecast day of each accuracy fold

    def fold_days(self, origin: int) -> range:
        return range(origin, origin + self.horizon)


def windows(cfg: dict) -> Windows:
    c = cfg["calendar"]
    last, h = c["last_day"], c["horizon_days"]
    sim_start = last - c["sim_blocks"] * h + 1
    seg_end = sim_start - 1
    seg_start = seg_end - c["segment_window_days"] + 1
    first_origin = sim_start - c["warmup_blocks"] * h
    n_origins = c["warmup_blocks"] + c["sim_blocks"]
    lgb_origins = tuple(first_origin + k * h for k in range(n_origins))
    eval_origins = tuple(last - (k + 1) * h + 1 for k in reversed(range(c["eval_folds"])))
    assert lgb_origins[-1] + h - 1 == last
    assert set(eval_origins) <= set(lgb_origins)
    return Windows(last, h, sim_start, last, seg_start, seg_end, lgb_origins, eval_origins)


def connect(cfg: dict, read_only: bool = False):
    import duckdb
    con = duckdb.connect(str(ROOT / cfg["paths"]["warehouse"]), read_only=read_only)
    con.execute(f"SET temp_directory = '{(ROOT / cfg['paths']['processed_dir'] / 'duckdb_tmp').as_posix()}'")
    con.execute("SET preserve_insertion_order = false")
    return con


def run_sql_file(con, sql_path: Path, params: dict | None = None) -> None:
    """Execute a SQL file. ${name} placeholders are substituted from params (integers only)."""
    text = Path(sql_path).read_text(encoding="utf-8")
    for k, v in (params or {}).items():
        text = text.replace("${" + k + "}", str(v))
    con.execute(text)
