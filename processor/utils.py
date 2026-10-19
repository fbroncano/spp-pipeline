import numpy as np

from datetime import datetime
from CONSTANTS import WGS84_A, WGS84_E2

def _day_year(day: int, month: int, year: int) -> str:
    day_dt = datetime(year, month, day)
    doy = day_dt.timetuple().tm_yday
    return f"{doy:03d}"


def datestr_filename(day: int, month: int, year: int) -> str:
    return f"{year}{month:02d}{day:02d}"


def nav_filename(station: str, day: int, month: int, year: int) -> str:
    doy_str = _day_year(day, month, year)
    return f"{station}00ESP_R_{year}{doy_str}0000_01D_MN.rnx.gz"


def obs_filename(station: str, day: int, month: int, year: int) -> str:    
    doy_str = _day_year(day, month, year)
    return f"{station}00ESP_R_{year}{doy_str}0000_01D_30S_MO.crx.gz"


def print_log(info, verbose: bool = True):
    if verbose:
        print("[SPP] ", info)


def subtract_gps_dates(t1: int, t2: int) -> int:
    dt = t1 - t2

    if dt > 302400:
        dt -= 604800
    elif dt < -302400:
        dt += 604800

    return dt


def seconds_gps_week(dt: datetime) -> float:
    """
    Seconds elapsed since the start of the GPS week.
    The GPS week starts on Sunday at 00:00:00.
    
    Args:
        dt: Date and time as datetime
        
    Returns:
        Seconds since the start of the GPS week (0-604799)
    """
    # Python weekday: 0=Monday, 6=Sunday
    # GPS: 0=Sunday, 1=Monday, ..., 6=Saturday
    weekday = (dt.weekday() + 1) % 7
    
    # Seconds since the start of the week
    seconds = (weekday * 86400 +  # full days
              dt.hour * 3600 +     # hours
              dt.minute * 60 +     # minutes
              dt.second +          # seconds
              dt.microsecond / 1e6)  # microseconds
    
    return seconds


def cvt_datetime(time: str) -> datetime:
    return datetime.strptime(time, "%Y-%m-%d %H:%M:%S")


def get_beidou_orbit_type(prn_number: int) -> str:
    """
    Orbit type of a BeiDou satellite.
    
    Parameters:
    -----------
    prn_number : int
        PRN number (e.g. 30 for C30)
    
    Returns:
    --------
    str : 'GEO', 'IGSO', o 'MEO'
    """
    if prn_number <= 5 or 59 <= prn_number <= 63:
        return 'GEO'
    elif 6 <= prn_number <= 10 or 38 <= prn_number <= 40:
        return 'IGSO'
    else:
        return 'MEO'


def get_beidou_valid(sv: str) -> bool:
    if len(sv) == 3:
        prn = int(sv.removeprefix('C'))
        return True if get_beidou_orbit_type(prn) == 'MEO' else False
    return False


def ecef_to_geodetic(x, y, z):

    lon = np.arctan2(y, x)
    
    p = np.sqrt(x**2 + y**2)
    
    # Bowring iteration
    lat = np.arctan2(z, p * (1 - WGS84_E2))
    
    for _ in range(10):
        N = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat)**2)
        lat_new = np.arctan2(z + WGS84_E2 * N * np.sin(lat), p)
        if abs(lat_new - lat) < 1e-12:
            break
        lat = lat_new
    
    N = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat)**2)
    h = p / np.cos(lat) - N
    
    return lat, lon, h

# GLONASS frequencies (FDMA, depending on channel k)
def glonass_frequency(band: int, k: int) -> np.float64:
    """
    GLONASS frequency for a band and channel k (-7 to +6).
    
    band: 1, 2 or 3
    k: frequency channel (FreqNum in RINEX)
    """
    if band == 1:
        return (1602.0 + k * 0.5625) * 1e6
    elif band == 2:
        return (1246.0 + k * 0.4375) * 1e6
    elif band == 3:
        return 1202.025e6  # CDMA, fixed frequency
    else:
        raise ValueError(f"Unknown band: {band}")
    
    
def pz90_to_wgs84(x_pz90, y_pz90, z_pz90):
    """
    Transforms coordinates from PZ-90.11 to WGS84.
    
    The transformation parameters are very small for PZ-90.11.
    For earlier versions (PZ-90.02) they are larger.
    """
    
    # PZ-90.11 -> WGS84 (ITRF2008) transformation parameters
    # Source: GLONASS ICD
    dx = -0.003  # metres
    dy = +0.001  # metres
    dz = +0.000  # metres
    
    # Rotations (~0 for PZ-90.11)
    rx = 0.0  # radians
    ry = 0.0  # radians
    rz = 0.0  # radians
    
    # Scale factor
    ds = 0.0  # ppm
    
    # Helmert transformation (small-rotation approximation)
    x_wgs84 = dx + (1 + ds * 1e-6) * (x_pz90 - rz * y_pz90 + ry * z_pz90)
    y_wgs84 = dy + (1 + ds * 1e-6) * (rz * x_pz90 + y_pz90 - rx * z_pz90)
    z_wgs84 = dz + (1 + ds * 1e-6) * (-ry * x_pz90 + rx * y_pz90 + z_pz90)
    
    return x_wgs84, y_wgs84, z_wgs84

