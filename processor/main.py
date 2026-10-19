import argparse
import datetime
import os

from dao import Database, DailyPositionDAO, DailyStatisticsDAO, RunDAO
from models import DailyPosition, DailyStatistics
from datetime import date
from manage_files import open_file, remove_file
from gnss_processor import process_all_epochs_with_hatch
from post_processor import calculate_statistics, calculate_final_position_robust
import time
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed
import logging
from multiprocessing import Manager
from logging.handlers import QueueListener, QueueHandler
from CONSTANTS import (SAVE, INTERVAL, MAX_WORKERS, DB_CONFIG_PATH, DB_HOST,
                       DB_PORT, DB_DATABASE, DB_USER)


def _nan_to_none(value):
    """NaN -> None, so that the value is stored as NULL and not as 'NaN'."""
    return None if value is None or (isinstance(value, float) and np.isnan(value)) else float(value)

SW_ID = 4  # 1.4.0: Galileo E1/E5b and system weighting (1.3.0: BeiDou TGD; 1.2.0: Hatch on the ionosphere-free combination)
EP_ID = 1
FRAME_ID = 4

METHODS = ['mean', 'median', 'weighted', 'mad', 'weighted_mad']
CONS_ID = {'all': 0, 'G': 1, 'E': 2, 'C': 3}
METHODS_ID = {'mean': 1, 'median': 2, 'weighted': 3, 'mad': 4, 'weighted_mad': 5}

# ERGNSS stations of the validation: peninsula, Balearic and Canary Islands.
STATIONS = ['ACOR', 'ALAC', 'ALME', 'CACE', 'CANT', 'HUEL', 'IBIZ', 'JACA',
            'LPAL', 'MALA', 'MALL', 'TAR0', 'YEBE']

# Validation period: the years 2024 and 2025.
START = date(2024, 1, 1)
END = date(2025, 12, 31)


def process_day(station: str, day: int, month: int, year: int, run_id: int) -> None:
    db = Database()
    position_dao = DailyPositionDAO(db)
    statistics_dao = DailyStatisticsDAO(db)

    # Constellations
    constellations = ['G', 'E', 'C']

    # Download the required files
    logging.info(f"Downloading observation file {station} {day:02d}/{month:02d}/{year}")
    obs_df, obs_attrs = open_file(station, day, month, year, False, SAVE)

    logging.info(f"Downloading navigation file {station} {day:02d}/{month:02d}/{year}")
    nav_df, nav_attrs = open_file(station, day, month, year, True, SAVE)

    # Process both files
    logging.info(f"Processing data of {station} {day:02d}/{month:02d}/{year}")
    day_m = process_all_epochs_with_hatch(obs_df, nav_df, obs_attrs['position'], constellations)

    # Store the results in the database
    for cons in day_m.keys():
        positions = day_m[cons]["positions"]
        metadata = day_m[cons]["metadata"]

        if len(positions) == 0:
            continue

        # Store statistics
        stats_dict = calculate_statistics(metadata)
        stats = DailyStatistics(
            station_id=station,
            date=date(year, month, day),
            constellation_id=CONS_ID[cons],
            run_id=run_id,
            n_satellites_avg=_nan_to_none(stats_dict['n_sat']),
            clock_bias_m=_nan_to_none(stats_dict['clksv']),
            gdop_avg=_nan_to_none(stats_dict['gdop_avg']),
            gdop_p95=_nan_to_none(stats_dict['gdop_p95']),
            gdop_max=_nan_to_none(stats_dict['gdop_max']),
            pdop_avg=_nan_to_none(stats_dict['pdop_avg']),
            pdop_p95=_nan_to_none(stats_dict['pdop_p95']),
            pdop_max=_nan_to_none(stats_dict['pdop_max']),
            tdop_avg=_nan_to_none(stats_dict['tdop_avg']),
            tdop_p95=_nan_to_none(stats_dict['tdop_p95']),
            tdop_max=_nan_to_none(stats_dict['tdop_max']),
            hdop_avg=_nan_to_none(stats_dict['hdop_avg']),
            hdop_p95=_nan_to_none(stats_dict['hdop_p95']),
            hdop_max=_nan_to_none(stats_dict['hdop_max']),
            vdop_avg=_nan_to_none(stats_dict['vdop_avg']),
            vdop_p95=_nan_to_none(stats_dict['vdop_p95']),
            vdop_max=_nan_to_none(stats_dict['vdop_max']),
            rms_avg=_nan_to_none(stats_dict['rms_avg']),
            rms_p95=_nan_to_none(stats_dict['rms_p95']),
            rms_max=_nan_to_none(stats_dict['rms_max']),
            elev_avg=_nan_to_none(stats_dict.get('elev_avg')),
            elev_min=_nan_to_none(stats_dict.get('elev_min')),
            n_sat_low_avg=_nan_to_none(stats_dict.get('n_sat_low_avg')),
            azim_resultant=_nan_to_none(stats_dict.get('azim_resultant')),
            azim_direction=_nan_to_none(stats_dict.get('azim_direction')),
            n_sat_g_avg=_nan_to_none(stats_dict.get('n_sat_g_avg')),
            n_sat_e_avg=_nan_to_none(stats_dict.get('n_sat_e_avg')),
            n_sat_c_avg=_nan_to_none(stats_dict.get('n_sat_c_avg')),
        )
        statistics_id = statistics_dao.insert(stats)
        logging.info(f"Inserted statistics with ID: {statistics_id}")

        # Store each method
        for method in METHODS:
            result = calculate_final_position_robust(positions, metadata, method=method)

            pos = DailyPosition(
                station_id=station,
                date=date(year, month, day),
                constellation_id=CONS_ID[cons],
                method_id=METHODS_ID[method],
                x=float(result['position'][0]),
                y=float(result['position'][1]),
                z=float(result['position'][2]),
                std_x=float(result['std_xyz'][0]),
                std_y=float(result['std_xyz'][1]),
                std_z=float(result['std_xyz'][2]),
                std_3d=float(result['std_3d']),
                n_epochs=int(result['n_epochs_total']),
                n_epochs_used=int(result['n_epochs_used']),
                run_id=run_id,
            )

            position_id = position_dao.insert(pos)
            logging.info(f"Inserted {method} position with ID: {position_id}")

    # Close the database connection
    db.close()

    # Remove the downloaded files
    remove_file(station, day, month, year)
    remove_file(station, day, month, year, True)


def task(station: str, day: int, month: int, year: int, run_id: int, log_queue):
    logger = logging.getLogger()
    logger.handlers = []
    logger.addHandler(QueueHandler(log_queue))
    logger.setLevel(logging.INFO)

    inicio_st = time.perf_counter()
    logging.info(f"Processing station {station} {day:02d}/{month:02d}/{year}")

    process_day(station=station, day=day, month=month, year=year, run_id=run_id)
    fin_st = time.perf_counter()

    logging.info(
        f"Finished station {station} "
        f"{day:02d}/{month:02d}/{year} "
        f"in {fin_st - inicio_st:.4f}s"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Daily SPP processing of EPN stations, written to PostGIS")
    parser.add_argument("--stations", nargs="+", default=STATIONS)
    parser.add_argument("--start", type=date.fromisoformat, default=START,
                        help="first day, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, default=END,
                        help="last day, YYYY-MM-DD (inclusive)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = parser.parse_args()

    os.makedirs("logs", exist_ok=True)
    manager = Manager()
    log_queue = manager.Queue()

    file_handler = logging.FileHandler(
        f"logs/execution_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(process)d] %(message)s"))
    listener = QueueListener(log_queue, file_handler, logging.StreamHandler())
    listener.start()

    # The main process writes through the same queue as the workers, so that its
    # messages end up in the file and not only on the console.
    root = logging.getLogger()
    root.handlers = [QueueHandler(log_queue)]
    root.setLevel(logging.INFO)

    # One row per run, created here and not inside the workers: if each process
    # created its own there would be one per station-day instead of one per
    # launch.
    run_db = Database()
    run_id = RunDAO(run_db).create(sw_id=SW_ID, ep_id=EP_ID,
                                   decimation_s=INTERVAL, frame_id=FRAME_ID)
    run_db.close()

    # Record the target in the log itself: if a long run ends up in the wrong
    # place, the log says so instead of it having to be deduced afterwards.
    logging.info(f"[MAIN] Stations: {' '.join(args.stations)}")
    logging.info(f"[MAIN] Period: {args.start} to {args.end}")
    logging.info(f"[MAIN] Database {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_DATABASE}")
    logging.info(f"[MAIN] Configuration read from {DB_CONFIG_PATH}")
    logging.info(f"[MAIN] Run registered with run_id={run_id}, "
                 f"decimation {INTERVAL}s, {args.workers} processes")

    n_days = (args.end - args.start).days + 1
    t_start = time.perf_counter()
    try:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = [
                executor.submit(task, station, d.day, d.month, d.year, run_id, log_queue)
                for station in args.stations
                for d in (args.start + datetime.timedelta(days=k) for k in range(n_days))
            ]
            # A failing day (a file missing on the server, for example) does
            # not stop the run, but it is logged.
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logging.error(f"[WORKER ERROR] {e}")
    finally:
        elapsed = time.perf_counter() - t_start
        logging.info(f"[MAIN] {len(args.stations) * n_days} station-days in {elapsed:.1f} s")
        listener.stop()


if __name__ == '__main__':
    main()
