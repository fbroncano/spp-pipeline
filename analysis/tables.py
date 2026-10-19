"""Tables of the validation, computed from the results database.

Every error is measured against the published station coordinate (ITRF2014)
translated to the antenna reference point in force on the date
(station_reference), and decomposed into East, North and Up with ecef_to_enu,
both from the GeoGNSS-PS schema.

Definitions used throughout:
  sigma_3D  daily 3D standard deviation of the epoch-wise solutions that the
            estimator keeps (after the MAD filter for methods 4 and 5),
            averaged over the sessions.
  eps_3D    3D distance between the daily position and the reference
            (daily error); summarised by its mean, median, 95th percentile
            and maximum over the sessions.
  rep_3D    day-to-day repeatability: 3D standard deviation of the daily
            ENU errors of a station, which does not depend on which epochs
            the estimator keeps.
  bias_3D   3D distance between the aggregated position (geometric median of
            the daily positions over the period) and the reference.
"""
import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

from .study import (CONSTELLATIONS, METHODS, RECOMMENDED_METHOD, day_filter,
                    params, query)


def processing_statistics(constellations=(1, 2, 3, 0), sampling=None):
    """Session statistics per station and constellation (Tables 3 and 4): median
    over the sessions of the daily values, since a few divergent epochs make the
    daily means and maxima meaningless (they are discarded later by the MAD filter)."""
    med = "percentile_cont(0.5) WITHIN GROUP (ORDER BY {})"
    sql = f"""
        SELECT ds.station_id, ds.constellation_id,
               COUNT(*)                                AS n,
               {med.format('ds.n_satellites_avg')}    AS sv,
               {med.format('ds.gdop_avg')}            AS gdop,
               {med.format('ds.pdop_avg')}            AS pdop,
               {med.format('ds.pdop_p95')}            AS pdop_p95,
               {med.format('ds.tdop_avg')}            AS tdop,
               {med.format('ds.rms_avg')}             AS rms,
               {med.format('ds.rms_p95')}             AS rms_p95
        FROM   daily_statistics ds
        WHERE  {day_filter('ds', sampling)}
          AND  ds.constellation_id = ANY(%(cons)s)
        GROUP  BY 1, 2
        ORDER  BY 1, 2"""
    df = query(sql, {**params(), "cons": list(constellations)})
    df["constellation"] = df.constellation_id.map(CONSTELLATIONS)
    return df


def daily_errors(constellations=(0,), methods=(RECOMMENDED_METHOD,), sampling=None,
                 start=None, end=None):
    """One row per station, day, constellation and method, with its ENU error."""
    sql = f"""
        SELECT dp.station_id, dp.date, dp.constellation_id, dp.method_id,
               dp.std_x, dp.std_y, dp.std_z, dp.std_3d,
               e.east, e.north, e.up
        FROM   daily_positions dp,
        LATERAL ecef_to_enu(dp.position, station_reference(dp.station_id, dp.date)) e
        WHERE  {day_filter('dp', sampling)}
          AND  dp.constellation_id = ANY(%(cons)s)
          AND  dp.method_id = ANY(%(meth)s)"""
    df = query(sql, {**params(start, end), "cons": list(constellations), "meth": list(methods)})
    for c in ("std_x", "std_y", "std_z", "std_3d", "east", "north", "up"):
        df[c] = df[c].astype(float)
    df["err_3d"] = np.sqrt(df.east ** 2 + df.north ** 2 + df.up ** 2)
    return df


def aggregated_solutions(constellation=0, method=RECOMMENDED_METHOD, sampling=None,
                         start=None, end=None):
    """Geometric median of the daily positions over the period, per station,
    with its ENU offset from the reference (Tables 5, 6 and 7)."""
    sql = f"""
        WITH agg AS (
            SELECT dp.station_id,
                   COUNT(*)                                   AS n,
                   ST_GeometricMedian(ST_Collect(dp.position)) AS p_med,
                   AVG(dp.std_x) AS sx, AVG(dp.std_y) AS sy, AVG(dp.std_z) AS sz,
                   AVG(dp.std_3d) AS s3d,
                   MAX(dp.date)  AS last_day
            FROM   daily_positions dp
            WHERE  {day_filter('dp', sampling)}
              AND  dp.constellation_id = %(cons)s
              AND  dp.method_id = %(meth)s
            GROUP  BY dp.station_id
        )
        SELECT a.station_id, a.n,
               ST_X(a.p_med) AS x, ST_Y(a.p_med) AS y, ST_Z(a.p_med) AS z,
               a.sx, a.sy, a.sz, a.s3d,
               ST_X(ST_Transform(r.ref, 4979)) AS lon,
               ST_Y(ST_Transform(r.ref, 4979)) AS lat,
               ST_Z(ST_Transform(r.ref, 4979)) AS h,
               e.east, e.north, e.up,
               ST_3DDistance(a.p_med, r.ref) AS bias_3d
        FROM   agg a,
        LATERAL (SELECT station_reference(a.station_id, a.last_day) AS ref) r,
        LATERAL ecef_to_enu(a.p_med, r.ref) e
        ORDER  BY a.station_id"""
    df = query(sql, {**params(start, end), "cons": constellation, "meth": method})
    df = df.astype({c: float for c in df.columns if c not in ("station_id", "n")})
    df["horizontal"] = np.hypot(df.east, df.north)
    return df


def estimator_comparison(sampling=None):
    """Accuracy and repeatability of the five estimators on the
    multi-constellation solution (Table 5).

    Returns the summary per estimator (sigma_3D, distribution of eps_3D over
    all sessions, median over stations of rep_3D), the per-station table
    (median eps_3D, 95th percentile and rep_3D) and paired Wilcoxon tests of
    the per-station median daily error, with the station as the unit of
    replication."""
    d = daily_errors(constellations=(0,), methods=tuple(METHODS), sampling=sampling)
    per_station = (d.groupby(["station_id", "method_id"])
                     .agg(sigma_3d=("std_3d", "mean"), eps_median=("err_3d", "median"),
                          eps_p95=("err_3d", lambda v: v.quantile(0.95))))
    rep = d.groupby(["station_id", "method_id"])[["east", "north", "up"]].std()
    per_station["rep_3d"] = np.sqrt((rep ** 2).sum(axis=1))
    per_station = per_station.reset_index()
    summary = (d.groupby("method_id").err_3d
                .agg(eps_mean="mean", eps_median="median",
                     eps_p95=lambda v: v.quantile(0.95), eps_max="max")
                .join(per_station.groupby("method_id")[["sigma_3d"]].mean())
                .join(per_station.groupby("method_id")[["rep_3d"]].median())
                .reset_index())
    summary.insert(1, "estimator", summary.method_id.map(METHODS))
    wide = per_station.pivot(index="station_id", columns="method_id", values="eps_median")
    tests = {}
    for a, b in ((4, 5), (2, 5), (2, 4)):
        tests[f"E{a}_vs_E{b}"] = {
            f"E{b}_better_stations": int((wide[b] < wide[a]).sum()), "n_stations": len(wide),
            "median_difference_m": float((wide[a] - wide[b]).median()),
            "wilcoxon_p": float(wilcoxon(wide[a], wide[b]).pvalue)}
    return summary, per_station, tests


def interannual(constellation=0, method=RECOMMENDED_METHOD, sampling=None):
    """ENU offset of the aggregated solution computed separately per year (Table 8)."""
    from datetime import date
    from .study import YEARS
    out = []
    for year in YEARS:
        a = aggregated_solutions(constellation, method, sampling, date(year, 1, 1), date(year, 12, 31))
        out.append(a[["station_id", "east", "north", "up", "horizontal"]].assign(year=year))
    return pd.concat(out).pivot(index="station_id", columns="year")


def constellation_accuracy(method=RECOMMENDED_METHOD, sampling=None):
    """Accuracy of each constellation and of the combination (asked by reviewer 2)."""
    rows = []
    daily = daily_errors(constellations=tuple(CONSTELLATIONS), methods=(method,), sampling=sampling)
    for cid, name in CONSTELLATIONS.items():
        a = aggregated_solutions(cid, method, sampling)
        d = daily[daily.constellation_id == cid]
        rows.append({"constellation": name, "stations": len(a), "sessions": len(d),
                     "east": a.east.mean(), "north": a.north.mean(), "up": a.up.mean(),
                     "horizontal": a.horizontal.mean(), "bias_3d": a.bias_3d.mean(),
                     "daily_err_3d_median": d.err_3d.median(),
                     "daily_err_3d_p95": d.err_3d.quantile(0.95),
                     "sigma_3d": a.s3d.mean()})
    return pd.DataFrame(rows)
