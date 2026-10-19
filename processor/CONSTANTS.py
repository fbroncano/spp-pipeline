import os
from pathlib import Path

import numpy as np


def _load_cfg(name="postgis.cfg"):
    """Looks for postgis.cfg from this folder upwards and reads it.

    With several databases running, hard-coding the connection invites a long
    reprocessing run against the wrong container. The file rules, and the
    environment overrides it when another target is needed without editing
    anything.
    """
    here = Path(__file__).resolve()
    for folder in [here.parent, *here.parents]:
        for candidate in (folder / name, folder / "GeoGNSS-PS" / name):
            if candidate.is_file():
                cfg = {}
                for line in candidate.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        cfg[key.strip()] = value.strip()
                cfg["_path"] = str(candidate)
                return cfg
    return {}


_CFG = _load_cfg()


def _setting(key, default):
    return os.environ.get(key) or _CFG.get(key, default)


# Database connection, taken from postgis.cfg unless the environment says
# otherwise. The last arguments are fallback defaults.
DB_CONFIG_PATH = _CFG.get("_path", "no postgis.cfg found, using defaults")
DB_DATABASE = _setting("POSTGRES_DB", "positions")
DB_USER = _setting("POSTGRES_USER", "postgres")
DB_PASSWORD = _setting("POSTGRES_PASSWORD", "")
DB_HOST = _setting("POSTGRES_HOST", "localhost")
DB_PORT = int(_setting("POSTGRES_PORT", 5432))
SAVE_FOLDER = "data"
DOWNLOAD_FOLDER = "downloads"
TEST = True
MAX_WORKERS = 12
SAVE = False
HATCH_EPOCHS = 10

# Observation weighting in the least-squares adjustment (see
# gnss_processor.observation_weights): 'none', 'system' or 'elevation'.
WEIGHT_MODEL = 'system'
ELEV_SIGMA_A = 0.3   # m, elevation-independent part of the code noise
ELEV_SIGMA_B = 0.3   # m, part scaled by 1/sin(E)
# Relative code noise per system, from the post-fit residual RMS of the
# single-system solutions corrected for degrees of freedom (37 station-days,
# 2024-2025, with TGD applied): GPS ~1.2 m, Galileo ~0.55 m, BeiDou ~1.3 m.
# Derived from the residuals, not tuned against the reference coordinates.
SYSTEM_SIGMA = {'G': 1.0, 'E': 0.5, 'C': 1.1, 'R': 1.0}
INTERVAL = 300

# WGS84 parameters
WGS84_A = 6378137.0  # Semi-major axis (m)
WGS84_F = 1 / 298.257223563  # Flattening
WGS84_E2 = 2 * WGS84_F - WGS84_F ** 2  # First eccentricity squared
WGS84_OMEGA_E = 7.2921151467e-5  # Earth rotation rate (rad/s)
WGS84_MU = 3.986005e14  # Gravitational parameter (m³/s²)

# Constants for orbit computation
PI = np.pi

# Elevation mask
ELEVATION_MASK = np.radians(10)

# Orbital constants
GNSS_CONSTANTS = {
    'G': {
        'mu': 3.986005e14,           # m³/s²
        'omega_e': 7.2921151467e-5,  # rad/s
        'F': -4.442807633e-10,       # s/m^(1/2)
    },
    'E': {
        'mu': 3.986004418e14,        # m³/s²
        'omega_e': 7.2921151467e-5,  # rad/s
        'F': -4.442807309e-10,       # s/m^(1/2)
    },
    'C': {
        'mu': 3.986004418e14,      # m³/s² (Earth GM)
        'omega_e': 7.2921150e-5,   # rad/s (Earth rotation rate)
        'F': -4.442807309e-10,     # s/m^(1/2) (relativistic factor)
    },
    'R': {
        'mu': 3.9860044e14,         # m³/s² - Gravitational constant
        'omega_e': 7.292115e-5,     # rad/s - Earth rotation rate
        'ae': 6378136.0,            # m - Equatorial radius
        'J2': 1.0826257e-3,         # Zonal harmonic coefficient J2
        'C20': -1.0826257e-3,       # C20 = -J2
    }
}

# Frequencies (Hz) and wavelengths
C = 299792458.0  # m/s

FREQUENCIES = {
    'G': {
        'C1': 1575.42e6,
        'C2': 1227.60e6,
        'C5': 1176.45e6,
    },
    'E': {
        'C1': 1575.42e6,   # = L1
        'C5': 1176.45e6,  # = L5
        'C7': 1207.14e6,
        'C6': 1278.75e6
    },
    'C': {
        'C1': 1575.42e6,
        'C5': 1176.45e6,
        'C2': 1561.098e6,
        'C6': 1268.52e6,
        'C7': 1207.14e6,
    },
}

# Order matters: the first pair available for the satellite is taken, so the
# first one must be the combination to which each system's broadcast clocks
# refer. Otherwise some satellites are resolved with one combination and others
# with another within the same epoch, and the uncorrected instrumental delays
# introduce a bias between groups that prevents convergence.
#
#   GPS      clocks and TGD refer to the L1/L2 P(Y) ionosphere-free combination
#   Galileo  F/NAV refers to E1/E5a (I/NAV uses E1/E5b, hence the BGD)
#   BeiDou   clocks refer to B3I; the standard pair is B1I/B3I
#
# ('C1','C5') used to come first for all three systems. At CACE, when the
# receiver was replaced on 2025-03-19, BDS-3 satellites switched to B1C/B2a
# while BDS-2 kept B1I/B3I: BeiDou stopped converging and disappeared from the
# database from that date.
IONOFREE_PAIRS = {
    'G': [('C1', 'C2'), ('C1', 'C5')],
    'E': [('C1', 'C7'), ('C1', 'C5'), ('C1', 'C6')],   # E1/E5b: reference of the I/NAV clocks
    'C': [('C2', 'C6'), ('C2', 'C7'), ('C1', 'C5')],
    'R': [('C1', 'C2')]
}

WAVELENGTHS = {
    'G': {
        'C1': C / 1575.42e6,  # ~0.190 m
        'C2': C / 1227.60e6,  # ~0.244 m
        'C5': C / 1176.45e6,  # ~0.255 m
    },
    'E': {
        'C1': C / 1575.42e6,
        'C5': C / 1176.45e6,
        'C7': C / 1207.14e6,
        'C6': C / 1278.75e6,
    },
    'C': {
        'C1': C / 1575.42e6,   # B1C (same as GPS L1)
        'C2': C / 1561.098e6,  # B1I
        'C5': C / 1176.45e6,   # B2a (same as GPS L5)
        'C6': C / 1268.52e6,   # B3I
        'C7': C / 1207.14e6,   # B2I
    }
}

# Code -> corresponding phase mapping
CODE_TO_PHASE = {
    'C1C': 'L1C',
    'C2W': 'L2W',
    'C5Q': 'L5Q',
    'C7Q': 'L7Q',
    'C6C': 'L6C',
}

TAU_CALC = {
    'G': ['C1', 'C5', 'C2'],
    'E': ['C5', 'C1', 'C7', 'C8', 'C6'],
    'C': ['C2', 'C5', 'C6', 'C1', 'C7'],
    'R': ['C1']
}