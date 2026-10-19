"""Loads the ERGNSS station catalogue into the stations table.

The CSV coordinates are in ITRF2014, so frame_id points to that realisation and
not to the one of the run. They are different things: the run records the frame
of the computed solutions, and the station the frame of its published
coordinate. Mixing them up is what later forces the frame to be inferred from
the data themselves.

Usage:
    python load_stations.py [csv_path] [--epoch 2010.0]
"""

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dao import Database

# Realisation of the file coordinates, not of the processing.
FRAME_ITRF2014 = 3

# Column widths declared in the table, to warn before the database truncates.
WIDTHS = {"name": 25, "city": 27, "province": 13}


def parse_number(text):
    """Decimal comma to float. The CSV uses the Spanish convention."""
    return float(text.strip().replace(".", "").replace(",", ".")) if text.strip() else None


def parse_date(text):
    """D/M/YY to date. Stations start from 1998, hence the pivot year."""
    text = text.strip()
    if not text:
        return None
    day, month, year = (int(p) for p in text.split("/"))
    return date(1900 + year if year >= 90 else 2000 + year, month, day)


def load(csv_path, epoch=None):
    db = Database()
    inserted, skipped = 0, []

    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))

    with db.get_cursor() as cursor:
        for row in rows:
            code = row["id"].strip()
            if len(code) != 4:
                skipped.append((code, "identifier is not 4 characters long"))
                continue

            values = {k: (row[k] or "").strip() for k in ("name", "city", "province")}
            for field, width in WIDTHS.items():
                if len(values[field]) > width:
                    skipped.append((code, f"{field} has {len(values[field])} characters, "
                                          f"the column allows {width}"))
                    values[field] = values[field][:width]

            try:
                x, y, z = (parse_number(row[c]) for c in ("x", "y", "z"))
            except ValueError as exc:
                skipped.append((code, f"unreadable coordinate: {exc}"))
                continue
            if None in (x, y, z):
                skipped.append((code, "no coordinates"))
                continue

            cursor.execute(
                """SELECT insert_station(
                       %s::char(4), %s::varchar(25), %s::varchar(27),
                       %s::varchar(13), %s::date, %s, %s, %s,
                       %s::smallint, %s::numeric(7,2))""",
                (code, values["name"], values["city"], values["province"],
                 parse_date(row["initial_date"]), x, y, z, FRAME_ITRF2014, epoch),
            )
            inserted += 1

    db.close()
    return inserted, skipped


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?",
                    default=str(Path(__file__).parent / "estaciones_itrf2014.csv"))
    ap.add_argument("--epoch", type=float, default=None,
                    help="epoch of the coordinates; left NULL if not given")
    args = ap.parse_args()

    n, skipped = load(args.csv, args.epoch)
    print(f"{n} stations written, frame ITRF2014, "
          f"epoch {args.epoch if args.epoch is not None else 'NULL'}")
    for code, reason in skipped:
        print(f"  warning {code}: {reason}")
