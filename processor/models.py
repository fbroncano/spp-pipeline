from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Station:
    id: str
    name: str
    city: str
    province: str
    initial_date: date
    x: float
    y: float
    z: float

    def __repr__(self):
        return f"Station({self.id}, {self.name}, {self.city}, {self.province})"


@dataclass
class Constellation:
    id: int
    code: str
    name: str

    def __repr__(self):
        return f"Constellation({self.id}, {self.code}, {self.name})"


@dataclass
class Method:
    id: int
    code: str
    description: str

    def __repr__(self):
        return f"Method({self.id}, {self.code})"


@dataclass
class DailyPosition:
    station_id: str
    constellation_id: int
    method_id: int
    x: float
    y: float
    z: float
    date: Optional[date] = None
    std_x: Optional[float] = None
    std_y: Optional[float] = None
    std_z: Optional[float] = None
    std_3d: Optional[float] = None
    n_epochs_used: Optional[int] = None
    n_epochs: Optional[int] = None
    # Run that produced the row. Assigned by the parent process at start-up and
    # carried down to here so that no row is left without its provenance.
    run_id: Optional[int] = None
    id: Optional[int] = None
    # Fields filled by queries
    constellation_code: Optional[str] = None
    constellation_name: Optional[str] = None
    method_code: Optional[str] = None

    def __repr__(self):
        return f"DailyPosition({self.station_id}, {self.date}, c={self.constellation_id}, m={self.method_id})"


@dataclass
class DailyStatistics:
    station_id: str
    date: date
    constellation_id: int
    n_satellites_avg: Optional[float] = None
    clock_bias_m: Optional[float] = None
    # Each metric is stored as mean, 95th percentile and maximum. The p95 is the
    # robust alternative to the maximum, which a single degenerate epoch inflates.
    gdop_avg: Optional[float] = None
    gdop_p95: Optional[float] = None
    gdop_max: Optional[float] = None
    pdop_avg: Optional[float] = None
    pdop_p95: Optional[float] = None
    pdop_max: Optional[float] = None
    tdop_avg: Optional[float] = None
    tdop_p95: Optional[float] = None
    tdop_max: Optional[float] = None
    hdop_avg: Optional[float] = None
    hdop_p95: Optional[float] = None
    hdop_max: Optional[float] = None
    vdop_avg: Optional[float] = None
    vdop_p95: Optional[float] = None
    vdop_max: Optional[float] = None
    rms_avg: Optional[float] = None
    rms_p95: Optional[float] = None
    rms_max: Optional[float] = None
    # Geometry of the satellites used, in degrees except azim_resultant, which
    # ranges from 0 (uniform sky) to 1 (satellites clustered in one direction).
    elev_avg: Optional[float] = None
    elev_min: Optional[float] = None
    n_sat_low_avg: Optional[float] = None
    azim_resultant: Optional[float] = None
    azim_direction: Optional[float] = None
    n_sat_g_avg: Optional[float] = None
    n_sat_e_avg: Optional[float] = None
    n_sat_c_avg: Optional[float] = None
    run_id: Optional[int] = None
    id: Optional[int] = None
    # Fields filled by queries
    constellation_code: Optional[str] = None
    constellation_name: Optional[str] = None

    def __repr__(self):
        return f"DailyStatistics({self.station_id}, {self.date}, c={self.constellation_id})"


@dataclass
class PositionError:
    constellation_code: str
    method: str
    error_x: float
    error_y: float
    error_z: float
    error_3d: float

    def __repr__(self):
        return f"PositionError({self.constellation_code}, {self.method}, error_3d={self.error_3d:.2f}m)"


@dataclass
class ConstellationStats:
    constellation_name: str
    n_days: int
    avg_error_3d: float
    avg_n_satellites: float
    avg_pdop: float
    avg_rms: float

    def __repr__(self):
        return f"ConstellationStats({self.constellation_name}, n_days={self.n_days}, error_3d={self.avg_error_3d:.2f}m)"