import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.extensions import register_adapter, AsIs
from contextlib import contextmanager
from models import *
from typing import List, Optional
from datetime import date
from CONSTANTS import *

# Register adapters for NumPy types
register_adapter(np.float64, lambda x: AsIs(float(x)))
register_adapter(np.float32, lambda x: AsIs(float(x)))
register_adapter(np.int64, lambda x: AsIs(int(x)))
register_adapter(np.int32, lambda x: AsIs(int(x)))


class Database:
    def __init__(self, host=DB_HOST, port=DB_PORT, database=DB_DATABASE,
                 user=DB_USER, password=DB_PASSWORD):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.connection = None

    def connect(self):
        if self.connection is None or self.connection.closed:
            self.connection = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password
            )
        return self.connection

    def close(self):
        if self.connection and not self.connection.closed:
            self.connection.close()

    @contextmanager
    def get_cursor(self, cursor_factory=RealDictCursor):
        conn = self.connect()
        cursor = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()


class StationDAO:
    def __init__(self, db: Database = Database()):
        self.db = db

    def insert(self, id: str, name: str, city: str, province: str,
               initial_date: date, x: float, y: float, z: float) -> None:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT insert_station(%s, %s, %s, %s, %s, %s, %s, %s)",
                (id, name, city, province, initial_date, x, y, z)
            )

    def update(self, id: str, x: float, y: float, z: float) -> None:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT update_station(%s, %s, %s, %s)",
                (id, x, y, z)
            )

    def get_by_id(self, id: str) -> Optional[Station]:
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM get_station(%s)", (id,))
            row = cursor.fetchone()
            if row:
                return Station(
                    id=row['station_id'].strip(),
                    name=row['name'],
                    city=row['city'],
                    province=row['province'],
                    initial_date=row['initial_date'],
                    x=row['x'],
                    y=row['y'],
                    z=row['z']
                )
            return None

    def get_all(self) -> List[Station]:
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, city, province, initial_date,
                       ST_X(location) as x, ST_Y(location) as y, ST_Z(location) as z
                FROM stations ORDER BY id
            """)
            return [
                Station(
                    id=row['id'].strip(),
                    name=row['name'],
                    city=row['city'],
                    province=row['province'],
                    initial_date=row['initial_date'],
                    x=row['x'],
                    y=row['y'],
                    z=row['z']
                )
                for row in cursor.fetchall()
            ]

    def delete(self, id: str) -> bool:
        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM stations WHERE id = %s", (id,))
            return cursor.rowcount > 0



class RunDAO:
    """Register of processor runs.

    Each launch of the program inserts one row here, and the identifier it
    returns accompanies every position and statistic written afterwards.
    Without it there is no way to tell which rows each run produced, and a fix
    to the processor can leave old rows coexisting with new ones with nothing
    to reveal it.
    """

    def __init__(self, db: Database = Database()):
        self.db = db

    def create(self, sw_id: int, ep_id: int, decimation_s: int,
               frame_id: Optional[int] = None) -> int:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """INSERT INTO runs (sw_id, ep_id, frame_id, decimation_s)
                   VALUES (%s::smallint, %s::smallint, %s::smallint, %s::integer)
                   RETURNING run_id""",
                (sw_id, ep_id, frame_id, decimation_s)
            )
            return cursor.fetchone()['run_id']

class DailyPositionDAO:
    def __init__(self, db: Database = Database()):
        self.db = db

    def insert(self, position: DailyPosition) -> int:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT insert_daily_position(%s::char(4), %s::date, %s::smallint, %s::smallint, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::integer)",
                (
                    position.station_id,
                    position.date,
                    position.constellation_id,
                    position.method_id,
                    position.x,
                    position.y,
                    position.z,
                    position.std_x,
                    position.std_y,
                    position.std_z,
                    position.std_3d,
                    position.n_epochs_used,
                    position.n_epochs,
                    position.run_id
                )
            )
            return cursor.fetchone()['insert_daily_position']

    def insert_batch(self, positions: List[DailyPosition]) -> List[int]:
        return [self.insert(pos) for pos in positions]

    def get_by_station_date(self, station_id: str, pos_date: date) -> List[DailyPosition]:
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM get_daily_positions(%s, %s)", (station_id, pos_date))
            return [
                DailyPosition(
                    station_id=station_id,
                    date=pos_date,
                    constellation_id=None,
                    method_id=None,
                    x=row['x'],
                    y=row['y'],
                    z=row['z'],
                    std_3d=float(row['std_3d']) if row['std_3d'] else None,
                    n_epochs_used=row['n_epochs_used'],
                    n_epochs=row['n_epochs'],
                    constellation_code=row['constellation_code'],
                    constellation_name=row['constellation_name'],
                    method_code=row['method']
                )
                for row in cursor.fetchall()
            ]

    def get_errors(self, station_id: str, pos_date: date) -> List[PositionError]:
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM get_position_errors(%s, %s)", (station_id, pos_date))
            return [
                PositionError(
                    constellation_code=row['constellation_code'],
                    method=row['method'],
                    error_x=row['error_x'],
                    error_y=row['error_y'],
                    error_z=row['error_z'],
                    error_3d=row['error_3d']
                )
                for row in cursor.fetchall()
            ]

    def delete_by_station_date(self, station_id: str, pos_date: date) -> int:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM daily_positions WHERE station_id = %s AND date = %s",
                (station_id, pos_date)
            )
            return cursor.rowcount

    def get_average_station(self, station_id: str, constellation_id: int, method_id: int) -> DailyPosition:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """SELECT station_id AS "Station",
                    ST_X(ST_GeometricMedian(ST_Collect(position::geometry)))::NUMERIC(10,2) AS x,
                    ST_Y(ST_GeometricMedian(ST_Collect(position::geometry)))::NUMERIC(10,2) AS y,
                    ST_Z(ST_GeometricMedian(ST_Collect(position::geometry)))::NUMERIC(10,2) AS z
                    FROM daily_positions dp
                    WHERE station_id = %s AND constellation_id = %s AND method_id = %s
                    GROUP BY station_id
                    ORDER BY station_id;""",
                (station_id, constellation_id, method_id))
            row = cursor.fetchone()
            return DailyPosition(station_id=station_id, constellation_id=constellation_id, method_id=method_id,
                                 x=row['x'], y=row['y'], z=row['z'])

class DailyStatisticsDAO:
    def __init__(self, db: Database = Database()):
        self.db = db

    def insert(self, stats: DailyStatistics) -> int:
        # Named rather than positional parameters: there are 23 consecutive
        # numeric parameters, and a transposition would raise no error, it
        # would just store VDOP where HDOP belongs.
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """SELECT insert_daily_statistics(
                    p_station_id       => %(station_id)s::char(4),
                    p_date             => %(date)s::date,
                    p_constellation_id => %(constellation_id)s::smallint,
                    p_n_satellites_avg => %(n_satellites_avg)s::numeric,
                    p_clock_bias_m     => %(clock_bias_m)s::numeric,
                    p_gdop_avg => %(gdop_avg)s::numeric,
                    p_gdop_p95 => %(gdop_p95)s::numeric,
                    p_gdop_max => %(gdop_max)s::numeric,
                    p_pdop_avg => %(pdop_avg)s::numeric,
                    p_pdop_p95 => %(pdop_p95)s::numeric,
                    p_pdop_max => %(pdop_max)s::numeric,
                    p_tdop_avg => %(tdop_avg)s::numeric,
                    p_tdop_p95 => %(tdop_p95)s::numeric,
                    p_tdop_max => %(tdop_max)s::numeric,
                    p_hdop_avg => %(hdop_avg)s::numeric,
                    p_hdop_p95 => %(hdop_p95)s::numeric,
                    p_hdop_max => %(hdop_max)s::numeric,
                    p_vdop_avg => %(vdop_avg)s::numeric,
                    p_vdop_p95 => %(vdop_p95)s::numeric,
                    p_vdop_max => %(vdop_max)s::numeric,
                    p_rms_avg  => %(rms_avg)s::numeric,
                    p_rms_p95  => %(rms_p95)s::numeric,
                    p_rms_max  => %(rms_max)s::numeric,
                    p_elev_avg       => %(elev_avg)s::numeric,
                    p_elev_min       => %(elev_min)s::numeric,
                    p_n_sat_low_avg  => %(n_sat_low_avg)s::numeric,
                    p_azim_resultant => %(azim_resultant)s::numeric,
                    p_azim_direction => %(azim_direction)s::numeric,
                    p_n_sat_g_avg    => %(n_sat_g_avg)s::numeric,
                    p_n_sat_e_avg    => %(n_sat_e_avg)s::numeric,
                    p_n_sat_c_avg    => %(n_sat_c_avg)s::numeric,
                    p_run_id         => %(run_id)s::integer
                )""",
                vars(stats)
            )
            return cursor.fetchone()['insert_daily_statistics']

    def insert_batch(self, stats_list: List[DailyStatistics]) -> List[int]:
        return [self.insert(stats) for stats in stats_list]

    def get_by_station_date(self, station_id: str, stats_date: date) -> List[DailyStatistics]:
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM get_daily_statistics(%s, %s)", (station_id, stats_date))
            return [
                DailyStatistics(
                    station_id=station_id,
                    date=stats_date,
                    constellation_id=None,
                    n_satellites_avg=float(row['n_satellites_avg']) if row['n_satellites_avg'] else None,
                    clock_bias_m=float(row['clock_bias_m']) if row['clock_bias_m'] else None,
                    gdop_avg=float(row['gdop_avg']) if row['gdop_avg'] else None,
                    pdop_avg=float(row['pdop_avg']) if row['pdop_avg'] else None,
                    pdop_max=float(row['pdop_max']) if row['pdop_max'] else None,
                    tdop_avg=float(row['tdop_avg']) if row['tdop_avg'] else None,
                    rms_avg=float(row['rms_avg']) if row['rms_avg'] else None,
                    rms_max=float(row['rms_max']) if row['rms_max'] else None,
                    constellation_code=row['constellation_code'],
                    constellation_name=row['constellation_name']
                )
                for row in cursor.fetchall()
            ]

    def get_constellation_stats(self, station_id: str, method_id: int = 5) -> List[ConstellationStats]:
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM get_constellation_stats(%s, %s)", (station_id, method_id))
            return [
                ConstellationStats(
                    constellation_name=row['constellation_name'],
                    n_days=row['n_days'],
                    avg_error_3d=row['avg_error_3d'],
                    avg_n_satellites=float(row['avg_n_satellites']) if row['avg_n_satellites'] else None,
                    avg_pdop=float(row['avg_pdop']) if row['avg_pdop'] else None,
                    avg_rms=float(row['avg_rms']) if row['avg_rms'] else None
                )
                for row in cursor.fetchall()
            ]

    def delete_by_station_date(self, station_id: str, stats_date: date) -> int:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM daily_statistics WHERE station_id = %s AND date = %s",
                (station_id, stats_date)
            )
            return cursor.rowcount