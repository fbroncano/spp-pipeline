import numpy as np
from utils import ecef_to_geodetic

def filter_by_pdop(positions, metadata, max_pdop=5.0):
    """Filters out epochs with high PDOP."""
    mask = np.array([m.get('pdop', 999) <= max_pdop for m in metadata])
    return positions[mask], [m for m, valid in zip(metadata, mask) if valid]


def filter_by_satellites(positions, metadata, min_sats=6):
    """Filters out epochs with few satellites."""
    mask = np.array([m.get('n_sat', 0) >= min_sats for m in metadata])
    return positions[mask], [m for m, valid in zip(metadata, mask) if valid]


def weighted_mean_position(positions, pdops):
    """
    Mean weighted by geometry quality (1/PDOP²).
    """
    positions = np.array(positions)
    
    # PDOP-based weights (lower PDOP = higher weight)
    pdops = np.array(pdops)
    weights = 1.0 / (pdops ** 2)

    # Normalise weights
    weights /= weights.sum()
    
    # Weighted mean
    if len(positions) > 0:
        pos_weighted = np.average(positions, axis=0, weights=weights)

        # Weighted STD
        variance = np.average((positions - pos_weighted) ** 2, axis=0, weights=weights)
        std_weighted = np.sqrt(variance)
    else:
        pos_weighted = [0, 0, 0]
        std_weighted = [0, 0, 0]


    
    return pos_weighted, std_weighted


def median_position(positions):
    """Per-coordinate median, very robust to outliers."""
    return np.median(positions, axis=0)


def robust_mad_filter(positions, threshold=3.0):
    """
    Filters outliers using the MAD (more robust than the STD).
    """
    positions = np.array(positions)
    
    # Median
    median_pos = np.median(positions, axis=0)
    
    # Distances to the median
    distances = np.linalg.norm(positions - median_pos, axis=1)
    
    # MAD (Median Absolute Deviation)
    mad = np.median(np.abs(distances - np.median(distances)))
    
    # Scale factor for equivalence with the STD (normal distribution)
    mad_std = 1.4826 * mad
    
    # Filter outliers
    mask = distances < (np.median(distances) + threshold * mad_std)
    
    return positions[mask], mask


def calculate_final_position_robust(positions, metadata, method='weighted_mad'):
    """
    Daily position from the epoch-wise solutions.
    
    Methods:
    - 'mean': arithmetic mean
    - 'median': per-coordinate median (robust)
    - 'weighted': PDOP-weighted mean
    - 'mad': MAD filter + mean
    - 'weighted_mad': MAD filter + PDOP-weighted mean
    """

    positions = np.array(positions)
    n_original = len(positions)
    
    if method == 'mean':
        pos_final = np.mean(positions, axis=0)
        std = np.std(positions, axis=0)
        n_used = n_original
        
    elif method == 'median':
        pos_final = np.median(positions, axis=0)
        std = np.std(positions, axis=0)
        n_used = n_original
        
    elif method == 'weighted':
        pos_final, std = weighted_mean_position(positions, metadata['pdop'])
        n_used = n_original
        
    elif method == 'mad':
        positions_filtered, mask = robust_mad_filter(positions)
        pos_final = np.mean(positions_filtered, axis=0)
        std = np.std(positions_filtered, axis=0)
        n_used = len(positions_filtered)
        
    elif method == 'weighted_mad':
        # 1. Filter outliers with the MAD
        positions_filtered, mask = robust_mad_filter(positions)
        pdops_filtered = [dop for dop, valid in zip(metadata['pdop'], mask) if valid]
        
        # 2. Weighted mean of the remaining epochs
        pos_final, std = weighted_mean_position(positions_filtered, pdops_filtered)
        n_used = len(positions_filtered)
    
    else:
        raise ValueError(f"Unknown method: {method}")
    
    n_rejected = n_original - n_used
    
    return {
        'position': pos_final if n_original > 0 else [0, 0, 0],
        'std_xyz': std if n_original > 0 else [0, 0, 0],
        'std_3d': np.linalg.norm(std) if n_original > 0 else 0,
        'n_epochs_total': n_original,
        'n_epochs_used': n_used,
        'n_rejected': n_rejected,
        'method': method,
    }


def calculate_statistics(metadata: dict) -> dict:
    return {
        'n_sat': np.average(metadata["n_sat"]),
        'clksv': np.average(metadata["clksv"]),

        # PDOP
        'pdop_avg': np.average(metadata["pdop"]),
        'pdop_p95': np.percentile(metadata["pdop"], 95),
        'pdop_max': np.max(metadata["pdop"], initial=0),

        # GDOP
        'gdop_avg': np.average(metadata["gdop"]),
        'gdop_p95': np.percentile(metadata["gdop"], 95),
        'gdop_max': np.max(metadata["gdop"], initial=0),

        # TDOP
        'tdop_avg': np.average(metadata["tdop"]),
        'tdop_p95': np.percentile(metadata["tdop"], 95),
        'tdop_max': np.max(metadata["tdop"], initial=0),

        # HDOP
        'hdop_avg': np.average(metadata["hdop"]),
        'hdop_p95': np.percentile(metadata["hdop"], 95),
        'hdop_max': np.max(metadata["hdop"], initial=0),

        # VDOP
        'vdop_avg': np.average(metadata["vdop"]),
        'vdop_p95': np.percentile(metadata["vdop"], 95),
        'vdop_max': np.max(metadata["vdop"], initial=0),

        # RMS
        'rms_avg': np.average(metadata["rms"]),
        'rms_p95': np.percentile(metadata["rms"], 95),
        'rms_max': np.max(metadata["rms"], initial=0),

        # Geometry
        **_aggregate_geometry(metadata),
    }


def _aggregate_geometry(metadata: dict) -> dict:
    """Aggregates the epoch-wise geometry statistics over the day."""
    if not metadata.get("elev_avg"):
        return {}

    # Vector mean of the azimuths: the components are averaged and the angle is
    # rebuilt at the end, never the other way round.
    azim_c = np.nanmean(metadata["azim_c"])
    azim_s = np.nanmean(metadata["azim_s"])

    return {
        'elev_avg': np.nanmean(metadata["elev_avg"]),
        'elev_min': np.nanmin(metadata["elev_min"]),
        'n_sat_low_avg': np.average(metadata["n_sat_low"]),
        'azim_resultant': float(np.hypot(azim_c, azim_s)),
        'azim_direction': float(np.degrees(np.arctan2(azim_s, azim_c)) % 360),
        'n_sat_g_avg': np.average(metadata["n_sat_g"]),
        'n_sat_e_avg': np.average(metadata["n_sat_e"]),
        'n_sat_c_avg': np.average(metadata["n_sat_c"]),
    }


def calculate_geometry(satellites_data, low_mask_deg=20.0):
    """Geometry of the satellites used in one epoch.

    Azimuthal asymmetry is summarised by the vector mean of the azimuths: the
    (cos, sin) components are stored instead of the angle, because azimuth is a
    circular quantity and averaging angles over the day would give meaningless
    results across 0/360°.

    The resultant length is 0 when the sky is uniformly covered and tends to 1
    when the satellites cluster in one direction, which is the situation that
    biases the horizontal component towards that side.
    """
    elev = np.array([s['elevation'] for s in satellites_data if s.get('elevation') is not None])
    azim = np.array([s['azimuth'] for s in satellites_data if s.get('azimuth') is not None])
    systems = [s['system'] for s in satellites_data]

    geometry = {
        'elev_avg': float(np.degrees(elev.mean())) if elev.size else np.nan,
        'elev_min': float(np.degrees(elev.min())) if elev.size else np.nan,
        'n_sat_low': int((elev < np.radians(low_mask_deg)).sum()) if elev.size else 0,
        'n_sat_g': systems.count('G'),
        'n_sat_e': systems.count('E'),
        'n_sat_c': systems.count('C'),
    }
    if azim.size:
        geometry['azim_c'] = float(np.cos(azim).mean())
        geometry['azim_s'] = float(np.sin(azim).mean())
    else:
        geometry['azim_c'] = geometry['azim_s'] = np.nan
    return geometry


def calculate_dop(HtH_inv, station_pos):
    """
    Dilution of precision factors.
    
    GDOP: Geometric (position + time)
    PDOP: Position (3D)
    HDOP: Horizontal
    VDOP: Vertical
    """
    
    # Variances in ECEF
    var_x = HtH_inv[0, 0]
    var_y = HtH_inv[1, 1]
    var_z = HtH_inv[2, 2]
    var_t = HtH_inv[3, 3]
    
    GDOP = np.sqrt(var_x + var_y + var_z + var_t)
    PDOP = np.sqrt(var_x + var_y + var_z)
    TDOP = np.sqrt(var_t)
    
    # HDOP and VDOP require a transformation to ENU
    lat, lon, _ = ecef_to_geodetic(*station_pos)
    
    # ECEF -> ENU rotation matrix
    sin_lat = np.sin(lat)
    cos_lat = np.cos(lat)
    sin_lon = np.sin(lon)
    cos_lon = np.cos(lon)
    
    R = np.array([
        [-sin_lon,              cos_lon,             0      ],
        [-sin_lat * cos_lon,   -sin_lat * sin_lon,   cos_lat],
        [ cos_lat * cos_lon,    cos_lat * sin_lon,   sin_lat]
    ])
    
    # Transform the covariance to ENU
    Q_xyz = HtH_inv[:3, :3]
    Q_enu = R @ Q_xyz @ R.T
    
    var_e = Q_enu[0, 0]
    var_n = Q_enu[1, 1]
    var_u = Q_enu[2, 2]
    
    HDOP = np.sqrt(var_e + var_n)
    VDOP = np.sqrt(var_u)
    
    return {
        'GDOP': GDOP,
        'PDOP': PDOP,
        'HDOP': HDOP,
        'VDOP': VDOP,
        'TDOP': TDOP,
    }
