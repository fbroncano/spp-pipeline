"""Comparison of the pipeline with RTKLIB on the same RINEX files.

RTKLIB's rnx2rtkp (an independent SPP implementation) processes a subset of
the validation, the first day of every month for the thirteen stations, with
the settings of spp.conf. Its daily position (median of the epoch-wise
solutions) and the pipeline's are compared against the NGL PPP series of the
same day, station by station, and the processing time of both is measured on
the same files.

RTKLIB is not distributed with this code; build rnx2rtkp from
https://github.com/rtklibexplorer/RTKLIB (see README) and pass its path.

Usage (from the repository root):
    python -m analysis.rtklib download
    python -m analysis.rtklib run --rnx2rtkp /path/to/rnx2rtkp
    python -m analysis.rtklib compare [--out results]
    python -m analysis.rtklib timing --rnx2rtkp /path/to/rnx2rtkp [--days 24]
"""
import argparse
import gzip
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .. import study
from ..ngl import enu_errors, load_reference

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "data" / "rtklib"
RINEX, OUT = DATA / "rinex", DATA / "solutions"
BASE_URL = "https://datos-geodesia.ign.es/ERGNSS/diario_30s/{y}/{ymd}/{name}"
SYSTEMS = {"G": ("G", 1), "E": ("E", 2), "GE": ("G,E", None),
           "GEC": ("G,E,C", 0)}   # rnx2rtkp -sys, pipeline constellation_id


def subset_days():
    return [date(y, m, 1) for y in study.YEARS for m in range(1, 13)]


def names(station, day):
    stem = f"{station}00ESP_R_{day.year}{day.timetuple().tm_yday:03d}0000_01D"
    return f"{stem}_30S_MO.crx.gz", f"{stem}_MN.rnx.gz", stem


def _fetch(st, day, crx2rnx):
    obs, nav, stem = names(st, day)
    if (RINEX / f"{stem}_30S_MO.rnx").exists() and (RINEX / f"{stem}_MN.rnx").exists():
        return None
    for name in (obs, nav):
        url = BASE_URL.format(y=day.year, ymd=day.strftime("%Y%m%d"), name=name)
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(RINEX / name, "wb") as f:
                shutil.copyfileobj(r, f)
        except Exception:
            for n in (obs, nav):
                (RINEX / n).unlink(missing_ok=True)
            return f"missing on the server: {name}"
    for name in (obs, nav):
        with gzip.open(RINEX / name) as fi, open(RINEX / name[:-3], "wb") as fo:
            shutil.copyfileobj(fi, fo)
        (RINEX / name).unlink()
    subprocess.run([str(crx2rnx), "-f", "-d", str(RINEX / obs[:-3])], check=True)
    return f"{st} {day}"


def download(workers=8):
    from concurrent.futures import ThreadPoolExecutor
    RINEX.mkdir(parents=True, exist_ok=True)
    crx2rnx = Path(sys.executable).with_name("crx2rnx")   # from the hatanaka package
    jobs = [(st, day) for st in study.STATIONS for day in subset_days()]
    with ThreadPoolExecutor(workers) as ex:
        for msg in ex.map(lambda j: _fetch(*j, crx2rnx), jobs):
            if msg:
                print(msg, flush=True)


def run(rnx2rtkp):
    OUT.mkdir(parents=True, exist_ok=True)
    for obs in sorted(RINEX.glob("*_30S_MO.rnx")):
        nav = obs.with_name(obs.name.replace("_30S_MO", "_MN"))
        for tag, (sysopt, _) in SYSTEMS.items():
            out = OUT / f"{obs.name[:19]}_{tag}.pos"
            if out.exists():
                continue
            subprocess.run([rnx2rtkp, "-k", str(HERE / "spp.conf"), "-ti", "300", "-sys", sysopt,
                            "-o", str(out), str(obs), str(nav)],
                           check=True, stderr=subprocess.DEVNULL)


def rtklib_daily():
    rows = []
    for f in sorted(OUT.glob("*.pos")):
        st, y, doy, tag = f.stem[:4], int(f.stem[12:16]), int(f.stem[16:19]), f.stem.split("_")[-1]
        sol = pd.read_csv(f, sep=r"\s+", comment="%", header=None, usecols=[2, 3, 4, 5],
                          names=["x", "y", "z", "q"])
        sol = sol[sol.q == 5]
        if len(sol) < 100:
            continue
        day = date.fromordinal(date(y, 1, 1).toordinal() + doy - 1)
        rows.append({"station_id": st, "date": day, "system": tag,
                     "x": sol.x.median(), "y": sol.y.median(), "z": sol.z.median()})
    return pd.DataFrame(rows)


def pipeline_daily(method):
    sql = f"""
        SELECT dp.station_id, dp.date, dp.constellation_id,
               ST_X(dp.position) AS x, ST_Y(dp.position) AS y, ST_Z(dp.position) AS z
        FROM daily_positions dp
        WHERE {study.day_filter('dp', 'all')} AND dp.method_id = %(meth)s
          AND EXTRACT(DAY FROM dp.date) = 1"""
    df = study.query(sql, {**study.params(), "meth": method})
    return df.astype({"x": float, "y": float, "z": float})


def antenna_heights():
    return study.query("""SELECT station_id, valid_from, valid_to, delta_h FROM station_antenna
                          WHERE station_id = ANY(%(stations)s)""", study.params())


def with_delta_h(df, ant):
    def dh(row):
        a = ant[(ant.station_id == row.station_id) & (ant.valid_from <= row.date)
                & (ant.valid_to.isna() | (ant.valid_to > row.date))]
        return float(a.delta_h.iloc[-1]) if len(a) else 0.0
    df = df.copy()
    df["delta_h"] = df.apply(dh, axis=1)
    return df


def compare(out_dir):
    ref, ant = load_reference(), antenna_heights()
    ant["valid_from"] = pd.to_datetime(ant.valid_from, utc=True).dt.date
    ant["valid_to"] = pd.to_datetime(ant.valid_to, utc=True).dt.date
    rt = with_delta_h(rtklib_daily(), ant)
    frames = []
    for tag, (_, cid) in SYSTEMS.items():
        r = enu_errors(rt[rt.system == tag], ref).assign(engine="RTKLIB", system=tag)
        frames.append(r)
    for method in (2, study.RECOMMENDED_METHOD):
        p = with_delta_h(pipeline_daily(method), ant)
        for tag, (_, cid) in SYSTEMS.items():
            if cid is None:
                continue
            sub = p[p.constellation_id == cid].merge(rt[rt.system == tag][["station_id", "date"]])
            frames.append(enu_errors(sub, ref).assign(engine=f"pipeline E{method}", system=tag))
    d = pd.concat(frames)
    per_station = (d.groupby(["engine", "system", "station_id"])[["east", "north", "up", "err_3d"]]
                    .median().reset_index())
    summary = (d.groupby(["engine", "system"])
                .agg(sessions=("err_3d", "size"), daily_err_3d_median=("err_3d", "median"),
                     daily_err_3d_p95=("err_3d", lambda v: v.quantile(0.95)))
                .join(per_station.groupby(["engine", "system"])[["east", "north", "up"]].mean()))
    out = Path(out_dir) / "rtklib"
    out.mkdir(parents=True, exist_ok=True)
    per_station.to_csv(out / "rtklib_per_station.csv", index=False)
    summary.to_csv(out / "rtklib_summary.csv")
    print(summary.round(3).to_string())


def timing(rnx2rtkp, n_days):
    """Wall time per station-day of both engines on the same files, one process."""
    sys.path.insert(0, str(HERE.parents[1] / "processor"))
    import georinex as gr
    from gnss_processor import process_all_epochs_with_hatch
    from manage_files import clean_nav_df
    from CONSTANTS import INTERVAL

    files = sorted(RINEX.glob("*_30S_MO.rnx"))[:n_days]
    t_rtk = t_pipe = 0.0
    for obs in files:
        nav = obs.with_name(obs.name.replace("_30S_MO", "_MN"))
        t0 = time.perf_counter()
        subprocess.run([rnx2rtkp, "-k", str(HERE / "spp.conf"), "-ti", "300", "-sys", "G,E,C",
                        "-o", "/dev/null", str(obs), str(nav)], check=True, stderr=subprocess.DEVNULL)
        t_rtk += time.perf_counter() - t0
        t0 = time.perf_counter()
        o = gr.load(obs, use={"G", "E", "C"}, interval=INTERVAL)
        attrs = dict(o.attrs)
        nv = clean_nav_df(gr.load(nav, use={"G", "E", "C"}).to_dataframe().dropna(how="all"))
        process_all_epochs_with_hatch(o.to_dataframe().dropna(how="all"), nv,
                                      attrs["position"], ["G", "E", "C"])
        t_pipe += time.perf_counter() - t0
    print(f"{len(files)} station-days, one process: RTKLIB {t_rtk / len(files):.2f} s, "
          f"pipeline {t_pipe / len(files):.2f} s per station-day "
          f"(reading, decimation and processing; download and database excluded)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["download", "run", "compare", "timing"])
    ap.add_argument("--rnx2rtkp", default="rnx2rtkp")
    ap.add_argument("--out", default="results")
    ap.add_argument("--days", type=int, default=24)
    a = ap.parse_args()
    {"download": download, "run": lambda: run(a.rnx2rtkp), "compare": lambda: compare(a.out),
     "timing": lambda: timing(a.rnx2rtkp, a.days)}[a.step]()


if __name__ == "__main__":
    main()
