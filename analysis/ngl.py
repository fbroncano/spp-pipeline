"""Comparison of the daily positions with the PPP series of the Nevada Geodetic
Laboratory (NGL), an independent reference processed with precise products.

The NGL series (IGS20, .tenv3) are downloaded to data/ngl/<STATION>.csv; they are
a third-party product and are not redistributed with the code. NGL refers to the
monument mark and the SPP solution to the antenna reference point, so the
antenna height in force on each date is removed from the Up component.

Usage (from the repository root):
    python -m analysis.ngl [--download] [--sampling all|d1_15] [--out results]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

from . import study

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "ngl"
BASE_URL = "https://geodesy.unr.edu/gps_timeseries/IGS20/tenv3/IGS20/{station}.tenv3"
# IGS20 geographic 3D (EPSG:10177) to IGS20 geocentric (EPSG:10176).
_TO_ECEF = Transformer.from_crs("EPSG:10177", "EPSG:10176", always_xy=True)


def download(station):
    df = pd.read_csv(BASE_URL.format(station=station), sep=r"\s+")
    day = pd.to_datetime(df["YYMMMDD"], format="%y%b%d")
    day = day.mask(day > pd.Timestamp.now(), day - pd.DateOffset(years=100))
    x, y, z = _TO_ECEF.transform(df["_longitude(deg)"].to_numpy(),
                                 df["_latitude(deg)"].to_numpy(),
                                 df["__height(m)"].to_numpy())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{station}.csv"
    pd.DataFrame({"date": day.dt.date, "x": x, "y": y, "z": z}).to_csv(path, index=False)
    return path


def load_reference():
    frames = [pd.read_csv(DATA_DIR / f"{s}.csv").assign(station_id=s)
              for s in study.STATIONS if (DATA_DIR / f"{s}.csv").exists()]
    ref = pd.concat(frames)
    ref["date"] = pd.to_datetime(ref.date).dt.date
    return ref.rename(columns={"x": "nx", "y": "ny", "z": "nz"})


def daily_positions(method=study.RECOMMENDED_METHOD, sampling=None):
    sql = f"""
        SELECT dp.station_id, dp.date, dp.constellation_id,
               ST_X(dp.position) AS x, ST_Y(dp.position) AS y, ST_Z(dp.position) AS z,
               COALESCE((SELECT a.delta_h FROM station_antenna a
                         WHERE a.station_id = dp.station_id AND dp.date >= a.valid_from
                           AND (a.valid_to IS NULL OR dp.date < a.valid_to)
                         ORDER BY a.valid_from DESC LIMIT 1), 0) AS delta_h
        FROM   daily_positions dp
        WHERE  {study.day_filter('dp', sampling)} AND dp.method_id = %(meth)s"""
    df = study.query(sql, {**study.params(), "meth": method})
    return df.astype({"x": float, "y": float, "z": float, "delta_h": float})


def enu_errors(spp, ref):
    d = spp.merge(ref, on=["station_id", "date"])
    lon = np.arctan2(d.ny, d.nx)
    lat = np.arctan2(d.nz, np.hypot(d.nx, d.ny) * (1 - 0.00669437999014))
    dx, dy, dz = d.x - d.nx, d.y - d.ny, d.z - d.nz
    d["east"] = -np.sin(lon) * dx + np.cos(lon) * dy
    d["north"] = -np.sin(lat) * np.cos(lon) * dx - np.sin(lat) * np.sin(lon) * dy + np.cos(lat) * dz
    d["up"] = (np.cos(lat) * np.cos(lon) * dx + np.cos(lat) * np.sin(lon) * dy
               + np.sin(lat) * dz - d.delta_h)
    d["err_3d"] = np.sqrt(d.east ** 2 + d.north ** 2 + d.up ** 2)
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--download", action="store_true", help="download the NGL series first")
    ap.add_argument("--sampling", choices=["all", "d1_15"], default=study.SAMPLING)
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    if args.download:
        for s in study.STATIONS:
            print(download(s))
    out = Path(args.out) / args.sampling
    out.mkdir(parents=True, exist_ok=True)

    d = enu_errors(daily_positions(sampling=args.sampling), load_reference())
    per_station = (d.groupby(["constellation_id", "station_id"])[["east", "north", "up", "err_3d"]]
                    .median().reset_index())
    per_station["constellation"] = per_station.constellation_id.map(study.CONSTELLATIONS)
    per_station.to_csv(out / "ngl_per_station.csv", index=False)
    summary = (d.groupby("constellation_id")
                .agg(sessions=("err_3d", "size"),
                     daily_err_3d_median=("err_3d", "median"),
                     daily_err_3d_p95=("err_3d", lambda v: v.quantile(0.95)))
                .join(per_station.groupby("constellation_id")[["east", "north", "up"]].mean()))
    summary.index = summary.index.map(study.CONSTELLATIONS)
    summary.to_csv(out / "ngl_summary.csv")
    print(summary.round(3).to_string())


if __name__ == "__main__":
    main()
