"""Definition of the validation and access to the results database.

The connection uses the same settings as the processor (postgis.cfg, overridden
by the POSTGRES_* environment variables), so the analysis always reads the
database the processing wrote to.
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "processor"))
from CONSTANTS import DB_DATABASE, DB_HOST, DB_PASSWORD, DB_PORT, DB_USER  # noqa: E402

# Stations of the validation: peninsula, Balearic and Canary Islands.
STATIONS = ["ACOR", "ALAC", "ALME", "CACE", "CANT", "HUEL", "IBIZ", "JACA",
            "LPAL", "MALA", "MALL", "TAR0", "YEBE"]

START, END = date(2024, 1, 1), date(2025, 12, 31)
YEARS = (2024, 2025)

# Software version whose runs are analysed (software.sw_id): 4 = 1.4.0.
SW_ID = 4

# A session enters the analysis only if its solution has redundancy: at least
# this many satellites on average over the day (with four, the residuals are
# identically zero and no outlier can be detected).
MIN_SATELLITES = 5

CONSTELLATIONS = {0: "Multi-GNSS", 1: "GPS", 2: "Galileo", 3: "BeiDou"}
METHODS = {1: "Mean", 2: "Median", 3: "PDOP-weighted mean",
           4: "MAD + mean", 5: "MAD + PDOP-weighted mean"}
RECOMMENDED_METHOD = 5

# Daily sessions entering the analysis: 'all' days, or only the 1st and 15th of
# each month ('d1_15'), the sampling of the first submission.
SAMPLING = "all"


def connect():
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_DATABASE,
                            user=DB_USER, password=DB_PASSWORD)


def query(sql, params=None):
    with connect() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def day_filter(alias="dp", sampling=None):
    """SQL condition selecting the analysed sessions of the study."""
    sampling = sampling or SAMPLING
    cond = (f"{alias}.station_id = ANY(%(stations)s) "
            f"AND {alias}.date BETWEEN %(start)s AND %(end)s "
            f"AND {alias}.run_id IN (SELECT run_id FROM runs WHERE sw_id = %(sw_id)s) "
            f"AND EXISTS (SELECT 1 FROM daily_statistics q WHERE q.station_id = {alias}.station_id "
            f"AND q.date = {alias}.date AND q.constellation_id = {alias}.constellation_id "
            f"AND q.run_id = {alias}.run_id AND q.n_satellites_avg >= {MIN_SATELLITES})")
    if sampling == "d1_15":
        cond += f" AND EXTRACT(DAY FROM {alias}.date) IN (1, 15)"
    return cond


def params(start=None, end=None):
    return {"stations": STATIONS, "start": start or START, "end": end or END, "sw_id": SW_ID}
