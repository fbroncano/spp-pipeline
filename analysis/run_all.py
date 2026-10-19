"""Computes every table of the validation and writes it as CSV and LaTeX.

Usage (from the repository root):
    python -m analysis.run_all [--sampling all|d1_15] [--out results]
"""
import argparse
import json
from pathlib import Path

from . import study, tables


def write(df, out, name, index=False, floatfmt="%.2f"):
    df.to_csv(out / f"{name}.csv", index=index)
    (out / f"{name}.tex").write_text(
        df.to_latex(index=index, float_format=floatfmt.__mod__, escape=True))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sampling", choices=["all", "d1_15"], default=study.SAMPLING)
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    study.SAMPLING = args.sampling
    out = Path(args.out) / args.sampling
    out.mkdir(parents=True, exist_ok=True)

    stats = tables.processing_statistics()
    for f in out.glob("table5_estimators_components.*"):
        f.unlink()
    for f in out.glob("table5_wilcoxon_E4_E5.json"):
        f.unlink()
    write(stats[stats.constellation_id != 0].drop(columns="constellation_id"), out,
          "table3_statistics_single")
    write(stats[stats.constellation_id == 0].drop(columns=["constellation_id", "constellation"]),
          out, "table4_statistics_multi")

    est, per_station, test = tables.estimator_comparison()
    write(est, out, "table5_estimators", floatfmt="%.3f")
    write(per_station, out, "table5_estimators_per_station", floatfmt="%.3f")
    (out / "table5_wilcoxon.json").write_text(json.dumps(test, indent=2))

    sol = tables.aggregated_solutions()
    write(sol[["station_id", "n", "x", "y", "z", "sx", "sy", "sz", "s3d", "bias_3d"]], out,
          "table6_solutions", floatfmt="%.2f")
    write(sol[["station_id", "n", "lon", "lat", "h", "east", "north", "up", "horizontal"]], out,
          "table7_enu")
    write(tables.interannual().reset_index(), out, "table8_interannual")
    write(tables.constellation_accuracy(), out, "table_constellation_accuracy")

    summary = {"sampling": args.sampling, "sw_id": study.SW_ID,
               "mean_bias_3d_m": float(sol.bias_3d.mean()),
               "mean_horizontal_m": float(sol.horizontal.mean()),
               "mean_east_m": float(sol.east.mean()), "mean_north_m": float(sol.north.mean()),
               "mean_up_m": float(sol.up.mean()),
               "sigma_3d_range_m": [float(sol.s3d.min()), float(sol.s3d.max())],
               "estimator_test": test}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
