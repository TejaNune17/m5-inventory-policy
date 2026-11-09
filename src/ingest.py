"""Download M5 from Kaggle, unpivot sales to long format, and load the DuckDB warehouse.

Also records source-file row counts in `src_file_counts` for reconciliation in dq.py.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from common import ROOT, connect, load_config, path, run_sql_file


def download(cfg: dict) -> None:
    raw = path(cfg, "raw_dir")
    files = cfg["kaggle"]["files"]
    if all((raw / f).exists() for f in files):
        print("raw files present, skipping download")
        return
    # Kaggle CLI from the same environment; credentials from ~/.kaggle (access_token or kaggle.json).
    exe = Path(sys.executable).parent / ("kaggle.exe" if os.name == "nt" else "kaggle")
    comp = cfg["kaggle"]["competition"]
    for f in files:
        if (raw / f).exists():
            continue
        print(f"downloading {f}")
        subprocess.run([str(exe), "competitions", "download", comp, "-f", f, "-p", str(raw), "-q"], check=True)
        z = raw / f"{f}.zip"
        if z.exists():
            with zipfile.ZipFile(z) as zf:
                zf.extractall(raw)
            z.unlink()
    missing = [f for f in files if not (raw / f).exists()]
    if missing:
        raise FileNotFoundError(f"download incomplete, missing: {missing}")


def source_counts(cfg: dict) -> list[tuple[str, int, int]]:
    """(file, data rows, d_ columns) counted directly from the CSV text, independent of DuckDB."""
    raw = path(cfg, "raw_dir")
    out = []
    for f in cfg["kaggle"]["files"]:
        with open(raw / f, "r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            n = sum(1 for _ in reader)
        d_cols = sum(1 for h in header if h.startswith("d_"))
        out.append((f, n, d_cols))
    return out


def load(cfg: dict) -> None:
    raw = path(cfg, "raw_dir").as_posix()
    con = connect(cfg)
    run_sql_file(con, ROOT / "sql" / "00_schema.sql", {"raw": raw})
    con.execute("CREATE OR REPLACE TABLE src_file_counts (file VARCHAR, data_rows BIGINT, d_columns INTEGER)")
    con.executemany("INSERT INTO src_file_counts VALUES (?, ?, ?)", source_counts(cfg))
    for t in ["dim_calendar", "dim_item", "dim_store", "dim_series", "fact_price", "fact_sales"]:
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"{t}: {n:,} rows")
    con.close()


if __name__ == "__main__":
    cfg = load_config()
    download(cfg)
    load(cfg)
