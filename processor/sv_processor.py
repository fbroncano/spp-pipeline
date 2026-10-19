from CONSTANTS import GNSS_CONSTANTS
import pandas as pd
import numpy as np
from utils import subtract_gps_dates, get_beidou_valid
from glonass import calculate_glonass_position

def calculate_sv_pos_GPS(row: pd.DataFrame, obs_seconds: int, constellation: str) -> tuple[np.float64, np.float64, np.float64, np.float64, int]:
    # Constants of the corresponding constellation

    mu = GNSS_CONSTANTS[constellation]['mu']
    omega_e = GNSS_CONSTANTS[constellation]['omega_e']
    f = GNSS_CONSTANTS[constellation]['F']

    # 1. GPS time of week
    # ttime = seconds_gps_week(cvt_datetime(row.name[0]))

    # 2. Time from ephemeris reference epoch
    adjust = 14 if constellation == 'C' else 0
    tk = subtract_gps_dates(obs_seconds, row['Toe'] + adjust)

    # 3. Semi-major axis
    a = row['sqrtA'] ** 2

    # 4. Corrected mean motion
    n = np.sqrt(mu/a**3) + row['DeltaN']

    # 5. Mean anomaly at time tk
    m = row['M0'] + n * tk

    # 6. Kepler's equation for the eccentric anomaly
    ek = m
    for _ in range(10):
        ek_old = ek
        ek = m + row['Eccentricity'] * np.sin(ek)
        if abs(ek - ek_old) < 1e-12:
                break

    # 7. True anomaly
    sin_Ek = np.sin(ek)
    cos_Ek = np.cos(ek)
    v = np.arctan2(np.sqrt(1.0 - row['Eccentricity']**2) * sin_Ek, cos_Ek - row['Eccentricity'])

    # 8. Argument of latitude
    phik = v + row['omega']

    # 9. Second-order corrections to latitude, radius and inclination
    sin2phi = np.sin(2.0 * phik)
    cos2phi = np.cos(2.0 * phik)
    delta_uk = row['Cus'] * sin2phi + row['Cuc'] * cos2phi # Argument of latitude correction
    delta_rk = row['Crs'] * sin2phi + row['Crc'] * cos2phi # Radius correction
    delta_ik = row['Cis'] * sin2phi + row['Cic'] * cos2phi # Inclination correction

    # 10. Corrected argument of latitude, radius and inclination
    uk = phik + delta_uk
    rk = a * (1 - row['Eccentricity'] * np.cos(ek)) + delta_rk
    ik = row['Io'] + row['IDOT'] * tk + delta_ik

    # 11. Corrected longitude of the ascending node
    omegak = row['Omega0'] + (row['OmegaDot'] - omega_e) * tk - omega_e * row['Toe']

    # 12. Satellite position
    xsv = rk * np.cos(uk) * np.cos(omegak) - rk * np.sin(uk) * np.cos(ik) * np.sin(omegak)
    ysv = rk * np.cos(uk) * np.sin(omegak) + rk * np.sin(uk) * np.cos(ik) * np.cos(omegak)
    zsv = rk * np.sin(uk) * np.sin(ik)

    # 13. Satellite clock correction
    clksv = row['SVclockBias'] + row['SVclockDrift'] * tk + row['SVclockDriftRate'] * tk**2 + f * row['Eccentricity'] * row['sqrtA'] * sin_Ek

    return xsv, ysv, zsv, clksv, row['Toe']


def calculate_sv_position(row: pd.DataFrame, sv: str, obs_seconds: int, constellations=['G', 'E']) -> pd.Series:
    freqnum = np.nan

    match sv[0]:
        case "G":
            x, y, z, clk, toe = calculate_sv_pos_GPS(row, obs_seconds, 'G')
        case "E":
            x, y, z, clk, toe = calculate_sv_pos_GPS(row, obs_seconds, 'E')
        case "C":
            if get_beidou_valid(sv):
                x, y, z, clk, toe = calculate_sv_pos_GPS(row, obs_seconds, 'C')
            else:
                x, y, z, clk, toe = np.nan, np.nan, np.nan, np.nan, np.nan
        case "R":
            x, y, z, clk, toe, freqnum = calculate_glonass_position(row, obs_seconds)
        case _:
            return pd.Series({'xsv': np.nan, 'ysv': np.nan, 'zsv': np.nan, 'clksv': np.nan, 'Toe': np.nan}, name=row.name)

    return pd.Series({'xsv': x, 'ysv': y, 'zsv': z, 'clksv': clk, 'Toe': toe, 'freqnum': freqnum}, name=row.name)
