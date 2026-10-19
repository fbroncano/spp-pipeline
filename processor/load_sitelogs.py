"""Loads the antenna and receiver history from the EPN site logs.

The site logs (https://epncb.oma.be/ftp/station/log/) are the authoritative
source for the equipment: they carry the full history with installation and
removal dates, whereas the RINEX header only reflects the state on that day.

For the antenna, the key field is `Marker->ARP Up Ecc.`, the DELTA H that must
be removed from the SPP solution to take it from the phase centre to the
monument mark (projected onto the local vertical, not added to Z).

Usage:
    python load_sitelogs.py --dir <site_logs_folder> [--dry-run]
"""
import argparse
import glob
import os
import re
from datetime import datetime

from dao import Database

# Blocks "3.1", "4.12"... The format also includes empty templates numbered
# "3.x" and "4.x" that must be discarded.
BLOCK_RE = re.compile(r"^(\d+)\.(\d+)\s")
FIELD_RE = re.compile(r"^\s*(.+?)\s*:\s*(.*?)\s*$")
# The first line of each block carries the number before the field name
# ("3.1  Receiver Type : ..."), which must be removed for the key to match.
KEY_PREFIX_RE = re.compile(r"^\d+\.\w+\s+")

# Markers of unfilled fields in the format templates.
PLACEHOLDERS = ("CCYY-MM-DD", "(A20", "(A*", "(F8.4", "(CCYY")


def _parse_date(value):
    """Site log dates: '2025-03-19T13:00Z', '2023-09-13' or empty."""
    value = (value or "").strip()
    if not value or value.startswith(PLACEHOLDERS):
        return None
    for fmt in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%HZ"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_sitelog(path):
    """Returns (station_id, [antennas], [receivers]) from a site log."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()

    station_id = os.path.basename(path)[:4].upper()
    antennas, receivers = [], []
    section, block = None, {}

    def flush():
        if not block:
            return
        if section == 3 and block.get("Receiver Type"):
            receivers.append({
                "valid_from": _parse_date(block.get("Date Installed")),
                "valid_to": _parse_date(block.get("Date Removed")),
                "receiver_type": block.get("Receiver Type"),
                "firmware": block.get("Firmware Version"),
            })
        elif section == 4 and block.get("Antenna Type"):
            antenna = block["Antenna Type"]
            # "TRM29659.00     NONE" -> type and radome share the same field
            parts = antenna.split()
            antennas.append({
                "valid_from": _parse_date(block.get("Date Installed")),
                "valid_to": _parse_date(block.get("Date Removed")),
                "antenna_type": parts[0] if parts else None,
                "radome": parts[1] if len(parts) > 1 else None,
                "antenna_serial": block.get("Serial Number"),
                "delta_h": _parse_float(block.get("Marker->ARP Up Ecc. (m)")),
                "delta_n": _parse_float(block.get("Marker->ARP North Ecc(m)")),
                "delta_e": _parse_float(block.get("Marker->ARP East Ecc(m)")),
            })

    for line in lines:
        match = BLOCK_RE.match(line)
        if match:
            flush()
            section, block = int(match.group(1)), {}
        elif re.match(r"^\d+\.x\s", line):     # empty template: closes the block
            flush()
            section, block = None, {}

        if section in (3, 4):
            field = FIELD_RE.match(line)
            if field:
                key = KEY_PREFIX_RE.sub("", field.group(1))
                value = field.group(2)
                if value and not value.startswith(PLACEHOLDERS):
                    block[key] = value
    flush()

    # Without an installation date or a DELTA H the record cannot be used.
    antennas = [a for a in antennas if a["valid_from"] and a["delta_h"] is not None]
    receivers = [r for r in receivers if r["valid_from"]]
    return station_id, antennas, receivers


def load(directory, dry_run=False):
    db = Database()
    with db.get_cursor() as cursor:
        cursor.execute("SELECT id FROM stations")
        known = {r["id"].strip() for r in cursor.fetchall()}

    inserted_a = inserted_r = 0
    skipped = []
    for path in sorted(glob.glob(os.path.join(directory, "*.log"))):
        station_id, antennas, receivers = parse_sitelog(path)
        if station_id not in known:
            skipped.append(station_id)
            continue

        print(f"{station_id}: {len(antennas)} antennas, {len(receivers)} receivers")
        for a in antennas:
            # Explicit str(): date.__format__ interprets the specifier as
            # strftime, so a ':<10' would be printed literally.
            hasta = str(a["valid_to"].date()) if a["valid_to"] else "current"
            print(f"    antenna {a['valid_from'].date()} -> {hasta:<10} "
                  f"{a['antenna_type']:<16} DELTA H={a['delta_h']:.4f}")
        if dry_run:
            continue

        with db.get_cursor() as cursor:
            for a in antennas:
                cursor.execute(
                    """INSERT INTO station_antenna (station_id, valid_from, valid_to,
                           antenna_type, antenna_serial, radome, delta_h, delta_n, delta_e)
                       VALUES (%(sid)s, %(valid_from)s, %(valid_to)s, %(antenna_type)s,
                               %(antenna_serial)s, %(radome)s, %(delta_h)s, %(delta_n)s, %(delta_e)s)
                       ON CONFLICT (station_id, valid_from) DO UPDATE SET
                           valid_to = EXCLUDED.valid_to,
                           antenna_type = EXCLUDED.antenna_type,
                           antenna_serial = EXCLUDED.antenna_serial,
                           radome = EXCLUDED.radome,
                           delta_h = EXCLUDED.delta_h,
                           delta_n = EXCLUDED.delta_n,
                           delta_e = EXCLUDED.delta_e""",
                    {"sid": station_id, **a})
                inserted_a += 1
            for r in receivers:
                cursor.execute(
                    """INSERT INTO station_receiver (station_id, valid_from, valid_to,
                           receiver_type, firmware)
                       VALUES (%(sid)s, %(valid_from)s, %(valid_to)s, %(receiver_type)s, %(firmware)s)
                       ON CONFLICT (station_id, valid_from) DO UPDATE SET
                           valid_to = EXCLUDED.valid_to,
                           receiver_type = EXCLUDED.receiver_type,
                           firmware = EXCLUDED.firmware""",
                    {"sid": station_id, **r})
                inserted_r += 1

    print(f"\n{inserted_a} antennas and {inserted_r} receivers"
          f"{' (dry run)' if dry_run else ' loaded'}")
    if skipped:
        print(f"EPN stations not present in `stations`: {', '.join(sorted(skipped))}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Loads EPN site logs into the database")
    parser.add_argument("--dir", required=True, help="folder with the .log files")
    parser.add_argument("--dry-run", action="store_true", help="print only, do not write")
    args = parser.parse_args()
    load(args.dir, args.dry_run)
