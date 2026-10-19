import pandas as pd
import json
import numpy as np
from utils import seconds_gps_week, subtract_gps_dates, cvt_datetime, ecef_to_geodetic
from CONSTANTS import C, TAU_CALC, HATCH_EPOCHS, WEIGHT_MODEL, ELEV_SIGMA_A, ELEV_SIGMA_B, SYSTEM_SIGMA
from hatchfilter import HatchFilter
from sv_processor import calculate_sv_position
from pre_processor import get_ionofree_pseudorange, get_ionofree_pseudorange_smoothed, check_elevation_mask, saastamoinen_tropospheric_delay, apply_sagnac_correction, _resolve_code
from post_processor import calculate_dop, calculate_final_position_robust, calculate_statistics, calculate_geometry


def select_best_ephemeris(nav_df: pd.DataFrame, sv: str, obs_time_gps_seconds: int, max_age_hours=2, glonass: bool=False):
    # Filter by SV
    try:
        sat_data = nav_df.xs(sv, level='sv').copy()
    except KeyError:
        return None
    
    if sat_data.empty:
        return None

    c_toe = 'Toe' #'MessageFrameTime' if glonass else 'Toe'
    if glonass:
        sat_data['eph'] = sat_data.index
        sat_data['Toe'] = sat_data['eph'].apply(lambda ttime: seconds_gps_week(cvt_datetime(ttime)))

    sat_data['dt'] = sat_data[c_toe].apply(lambda toe: subtract_gps_dates(obs_time_gps_seconds, toe))
    sat_data['abs_dt'] = sat_data['dt'].abs()
    
    # Filter by maximum age
    sat_data = sat_data[sat_data['abs_dt'] <= max_age_hours * 3600]
    
    past_eph = sat_data[sat_data['dt'] >= 0]  # Toe <= obs_time
    
    try:
        if not past_eph.empty:
            # Prefer the most recent past ephemeris
            best_idx = past_eph['abs_dt'].idxmin()
        else:
            # If there is none, use the closest future one
            best_idx = sat_data['abs_dt'].idxmin()
    except ValueError:
        # print(f"No ephemeris for {sv}")
        return None
    
    return sat_data.loc[best_idx]


def calculate_position(obs_df: pd.DataFrame, nav_df: pd.DataFrame, position: list[np.float64], constellations=['E']) -> pd.DataFrame:

    equations = []

    for index, obs in obs_df.iterrows():
        if index[1][0] in constellations:
            # 1. Find the closest ephemeris for each observation, within limits
            obs_seconds = seconds_gps_week(cvt_datetime(index[0]))
            sv_raw = select_best_ephemeris(nav_df, index[1], obs_seconds)
            if sv_raw is None:
                continue
            # 2. Compute the position of that satellite
            sv_data = calculate_sv_position(sv_raw, index[1], obs_seconds, constellations)
            sv_pos = [sv_data["xsv"], sv_data["ysv"], sv_data["zsv"]]

            # 3. Process the pseudorange
            pseudorange_ionofree = get_ionofree_pseudorange(obs, index[1])
            
            # Check elevation
            valid, elevation, azimuth = check_elevation_mask(
                position, sv_pos
            )
                
            # If the measurement is valid, apply corrections and store the equation xsv, ysv, zsv, clksv, dobs
            if pseudorange_ionofree is not None and valid:
                lat, _, h = ecef_to_geodetic(*position)
                d_tropo = saastamoinen_tropospheric_delay(elevation, lat, h)
                sat_pos_corrected = apply_sagnac_correction(sv_pos, position)
                dobs = pseudorange_ionofree + C * sv_data["clksv"] - d_tropo

                equations.append({'sat_pos': sat_pos_corrected, 'd_obs': dobs, 'system': index[1][0]})

    # 4. Solve the system of equations
    return solve_position(equations, position)    


def _nueva_metadata() -> dict:
    """Epoch-wise metadata containers.

    Single definition: the individual constellations and the combined solution
    share the same set of keys, so that adding a new variable cannot leave one
    of them out.
    """
    return {clave: [] for clave in (
        "epoch", "clksv", "rms", "n_sat",
        "gdop", "tdop", "pdop", "hdop", "vdop",
        "elev_avg", "elev_min", "n_sat_low", "azim_c", "azim_s",
        "n_sat_g", "n_sat_e", "n_sat_c",
    )}


def calculate_equations_with_hatch(epoch, epoch_obs, nav_df, position_approx, constellations, hatch_filter):
    """
    Builds the observation equations of one epoch using the Hatch filter.
    """
    
    equations = []
    if type(epoch) == str:
        obs_seconds = seconds_gps_week(cvt_datetime(epoch))
    else:
        obs_seconds = seconds_gps_week(epoch)

    
    for sv, obs in epoch_obs.iterrows():
        if sv[0] not in constellations:
            continue

        for code in TAU_CALC[sv[0]]:
            _, tau = _resolve_code(obs, code)
            if not np.isnan(tau):
                break

        if np.isnan(tau):
            continue

        ttime = obs_seconds - (tau / C)
        eph = select_best_ephemeris(nav_df, sv, obs_seconds, glonass=(sv[0] == 'R'))

        if eph is None:
            continue

        sv_data = calculate_sv_position(eph, sv, ttime, constellations)
        sv_pos = [sv_data["xsv"], sv_data["ysv"], sv_data["zsv"]]

        # Hatch-smoothed version
        pseudorange_ionofree = get_ionofree_pseudorange_smoothed(obs, sv, hatch_filter, sv_data["freqnum"], eph)
        if pseudorange_ionofree is None:
            continue
        
        valid, elevation, azimuth = check_elevation_mask(position_approx, sv_pos)

        if not valid:
            continue


        lat, _, h = ecef_to_geodetic(*position_approx)
        d_tropo = saastamoinen_tropospheric_delay(elevation, lat, h)
        sat_pos_corrected = apply_sagnac_correction(sv_pos, position_approx)
        dobs = pseudorange_ionofree + C * sv_data["clksv"] - d_tropo

        # Elevation and azimuth travel with the equation so that the geometry of
        # the satellites that actually entered the adjustment can be described.
        equations.append({
            'sat_pos': sat_pos_corrected,
            'd_obs': dobs,
            'system': sv[0],
            'elevation': elevation,
            'azimuth': azimuth,
        })
    
    if len(equations) < 4:
        return None
    
    return equations

    if len(constellations) > 1:
        return solve_position_multi_gnss(equations, position_approx, constellations)
    else:
        return solve_position(equations, position_approx)
    


def observation_weights(satellites_data):
    """
    Weight of each observation, 1/sigma^2, for the least-squares adjustment.

    WEIGHT_MODEL = 'none' reproduces the unweighted solution (all weights 1),
    'system' applies only the per-system factor SYSTEM_SIGMA, and
    'elevation' uses sigma^2 = a^2 + b^2/sin^2(E), which down-weights low
    satellites, scaled per system by SYSTEM_SIGMA so that a noisier
    constellation weighs less in the combined solution. Only the relative
    weights matter for the position.
    """
    if WEIGHT_MODEL == 'none':
        return np.ones(len(satellites_data))
    if WEIGHT_MODEL == 'system':
        return np.array([1.0 / SYSTEM_SIGMA.get(sat['system'], 1.0) ** 2
                         for sat in satellites_data])
    sigma2 = np.array([
        (ELEV_SIGMA_A ** 2 + ELEV_SIGMA_B ** 2 / np.sin(sat['elevation']) ** 2)
        * SYSTEM_SIGMA.get(sat['system'], 1.0) ** 2
        for sat in satellites_data])
    return 1.0 / sigma2

def solve_position(sv_data, station_approx, constellation, max_iter=10, threshold=1e-4):
    """
    Solves the station position by iterative least squares.
    
    Parameters:
    -----------
    sv_data : list of dict
        Data of each satellite:
        [{'sat_pos': [x,y,z], 'd_obs': float, 'system': str, ...}, ...]
    station_approx : array [x, y, z]
        Approximate station position (metres)
    constellation : str
        System whose satellites are used ('G', 'E' or 'C')
    max_iter : int
        Maximum number of iterations
    threshold : float
        Convergence threshold (metres)
    
    Returns:
    --------
    dict : {
        'position': [x, y, z],
        'clock_bias': float,
        'residuals': array,
        'converged': bool,
        'iterations': int,
        'dop': dict (GDOP, PDOP, HDOP, VDOP)
    }
    """

    # Select satellites
    satellites_data = [x for x in sv_data if x['system'] == constellation]
    n_sat = len(satellites_data)

    if n_sat < 4:
        return None  # Not enough satellites
    
    # Initial state: [x, y, z, c*dt]
    # c*dt (metres) is estimated instead of dt (seconds) for better conditioning
    state = np.array([
        station_approx[0],
        station_approx[1],
        station_approx[2],
        0.0  # c*dt inicial = 0
    ])
    
    for iteration in range(max_iter):
        
        H = np.zeros((n_sat, 4))       # Design matrix (Jacobian)
        delta_rho = np.zeros(n_sat)    # Residual vector
        
        for i, sat in enumerate(satellites_data):
            if sat['system'] == constellation:
                xs, ys, zs = sat['sat_pos']
                d_obs = sat['d_obs']
                
                # Current position estimate
                x, y, z = state[0], state[1], state[2]
                cdt = state[3]
                
                # Estimated geometric range
                dx = xs - x
                dy = ys - y
                dz = zs - z
                rho_est = np.sqrt(dx**2 + dy**2 + dz**2)
                
                # Estimated pseudorange
                d_est = rho_est + cdt
                
                # Residual (observed - computed)
                delta_rho[i] = d_obs - d_est
                
                # Row of the Jacobian matrix (partial derivatives)
                H[i, 0] = -dx / rho_est  # ∂d/∂x
                H[i, 1] = -dy / rho_est  # ∂d/∂y
                H[i, 2] = -dz / rho_est  # ∂d/∂z
                H[i, 3] = 1.0            # ∂d/∂(c·dt)
        
        # Least-squares solution: H·Δx = Δρ
        # Δx = (H'H)^(-1) · H' · Δρ
        try:
            HtH = H.T @ H
            HtH_inv = np.linalg.inv(HtH)
            # Weighted solution; HtH_inv stays unweighted so that the DOP
            # factors keep describing geometry alone.
            W = observation_weights(satellites_data)
            HtW = H.T * W
            delta_x = np.linalg.solve(HtW @ H, HtW @ delta_rho)
        except np.linalg.LinAlgError:
            return None  # Matriz singular
        
        # Update state
        state += delta_x
        
        # Check convergence
        position_change = np.linalg.norm(delta_x[:3])
        
        if position_change < threshold:
            # Compute DOP
            dop = calculate_dop(HtH_inv, state[:3])
            
            # Final residuals
            residuals = delta_rho
            
            return {
                'position': state[:3],
                'clock_bias': state[3] / C,  # Convertir a segundos
                'clock_bias_m': state[3],    # En metros
                'residuals': residuals,
                'rms': np.sqrt(np.mean(residuals**2)),
                'converged': True,
                'iterations': iteration + 1,
                'dop': dop,
                'n_satellites': n_sat,
                'geometry': calculate_geometry(satellites_data),
            }
    
    # Did not converge
    return {
        'position': state[:3],
        'clock_bias': state[3] / C,
        'clock_bias_m': state[3],
        'residuals': delta_rho,
        'rms': np.sqrt(np.mean(delta_rho**2)),
        'converged': False,
        'iterations': max_iter,
        'dop': None,
        'n_satellites': n_sat,
    }



def solve_position_multi_gnss(satellites_data, station_approx, constellations, max_iter=10, threshold=1e-4):
    """
    Solves the position with several constellations, estimating inter-system biases.
    
    State: [x, y, z, c*dt_GPS, c*ISB_GAL, c*ISB_BDS, ...]
    """

    # Check beforehand that all constellations are present
    cons = []
    for constellation in constellations:
        data = [x for x in satellites_data if x['system'] == constellation]
        if len(data) > 0:
            cons.append(constellation)

    n_sat = len(satellites_data)
    
    if n_sat < 4:
        return None
    
    # Systems present
    systems = set(cons)
    
    # ISB columns (GPS is the reference and has no ISB)
    isb_systems = [s for s in ['E', 'C', 'R'] if s in systems]
    n_isb = len(isb_systems)
    n_params = 4 + n_isb  # x, y, z, dt_GPS + ISBs
    
    # System -> column index mapping
    isb_index = {sys: 4 + i for i, sys in enumerate(isb_systems)}
    
    # Initial state
    state = np.zeros(n_params)
    state[0:3] = station_approx
    # dt_GPS and ISBs initialised to 0
    
    for iteration in range(max_iter):
        
        H = np.zeros((n_sat, n_params))
        delta_rho = np.zeros(n_sat)
        
        for i, sat in enumerate(satellites_data):
            xs, ys, zs = sat['sat_pos']
            d_obs = sat['d_obs']
            sys = sat['system']
            
            x, y, z = state[0:3]
            cdt_gps = state[3]
            
            # ISB for this system (0 for GPS)
            if sys == 'G':
                cdt_total = cdt_gps
            else:
                cdt_total = cdt_gps + state[isb_index[sys]]
            
            # Geometric range
            dx = xs - x
            dy = ys - y
            dz = zs - z
            rho_est = np.sqrt(dx**2 + dy**2 + dz**2)
            
            # Estimated pseudorange
            d_est = rho_est + cdt_total
            
            # Residual
            delta_rho[i] = d_obs - d_est
            
            # Jacobian
            H[i, 0] = -dx / rho_est
            H[i, 1] = -dy / rho_est
            H[i, 2] = -dz / rho_est
            H[i, 3] = 1.0  # dt_GPS siempre
            
            # ISB if not GPS
            if sys != 'G' and sys in isb_index:
                H[i, isb_index[sys]] = 1.0
        
        # Solve
        try:
            HtH = H.T @ H
            HtH_inv = np.linalg.inv(HtH)
            # Weighted solution; HtH_inv stays unweighted so that the DOP
            # factors keep describing geometry alone.
            W = observation_weights(satellites_data)
            HtW = H.T * W
            delta_x = np.linalg.solve(HtW @ H, HtW @ delta_rho)

        except np.linalg.LinAlgError:
            print("Singular normal matrix")
            return None
        
        state += delta_x
        
        if np.linalg.norm(delta_x[:3]) < threshold:
            dop = calculate_dop(HtH_inv, state[:3])

            # Build result
            result = {
                'position': state[:3],
                'clock_bias': state[3] / C,
                'clock_bias_m': state[3],
                'residuals': delta_rho,
                'rms': np.sqrt(np.mean(delta_rho**2)),
                'converged': True,
                'iterations': iteration + 1,
                'n_satellites': n_sat,
                'dop': dop,
                'geometry': calculate_geometry(satellites_data),
            }
            
            # Add ISBs
            for sys, idx in isb_index.items():
                sys_name = {'E': 'GAL', 'C': 'BDS', 'R': 'GLO'}[sys]
                result[f'ISB_{sys_name}_m'] = state[idx]
                result[f'ISB_{sys_name}_ns'] = state[idx] / C * 1e9
            
            return result
    
    return {
        'position': state[:3],
        'clock_bias': state[3] / C,
        'residuals': delta_rho,
        'rms': np.sqrt(np.mean(delta_rho**2)),
        'converged': False,
        'iterations': max_iter,
        'n_satellites': n_sat,
    }


def process_observation(obs_df: pd.DataFrame, nav_df: pd.DataFrame, position: list[np.float64], constellations=['G', 'E']) -> tuple[np.ndarray, list]:
    
    positions = []
    metadata = []

    # Process each epoch with all available satellites
    for _, observations in obs_df.groupby(level='time'):
        result = calculate_position(observations, nav_df, position, constellations=constellations)
        
        if result is not None and result['converged'] and result['n_satellites'] >= 4:
            positions.append(result['position'])
            metadata.append({
                'rms': result['rms'],
                'n_sat': result['n_satellites'],
                'pdop': result.get('dop', {}).get('PDOP', np.nan) if result.get('dop') else np.nan
            })
    
    # Convert to NumPy arrays
    if positions:
        positions = np.array(positions)  # Shape: (n_epochs, 3)
        return positions, metadata
    
    return None, None


def process_all_epochs_with_hatch(obs_df, nav_df, position_approx, constellations: list =['G', 'E'], all: bool=True) -> dict:
    """
    Processes all epochs of a day with the Hatch filter.
    Epochs MUST be in chronological order.
    """
    
    # Sort by time
    epochs = sorted(obs_df.index.get_level_values(0).unique())

    # New filter for this session
    hatch = HatchFilter(max_epochs=HATCH_EPOCHS, cycle_slip_threshold=5.0)
    
    day = {constellation: {"positions": [], "metadata": _nueva_metadata()}
           for constellation in constellations}
    if all:
        day["all"] = {"positions": [], "metadata": _nueva_metadata()}

    for epoch in epochs:
        epoch_obs = obs_df.loc[epoch]
        
        equations = calculate_equations_with_hatch(
            epoch, epoch_obs, nav_df, position_approx, constellations, hatch
        )

        # None is returned when the epoch has fewer than 4 satellites. Without
        # this guard, a single poor epoch raised TypeError and the whole day was lost.
        if equations is None:
            continue

        for constellation in day.keys():

            if constellation == "all":
                result = solve_position_multi_gnss(equations, position_approx, constellations)
            else:
                result = solve_position(equations, position_approx, constellation)

            # Keep it if valid
            if result is not None and result['converged'] and result['n_satellites'] >= 4:
                day[constellation]["positions"].append(result['position'])
                day[constellation]["metadata"]["clksv"].append(result['clock_bias'])
                day[constellation]["metadata"]["epoch"].append(epoch)
                day[constellation]["metadata"]["rms"].append(result['rms'])
                day[constellation]["metadata"]["n_sat"].append(result['n_satellites'])
                day[constellation]["metadata"]["gdop"].append(result['dop']["GDOP"])
                day[constellation]["metadata"]["tdop"].append(result['dop']["TDOP"])
                day[constellation]["metadata"]["pdop"].append(result['dop']["PDOP"])
                day[constellation]["metadata"]["hdop"].append(result['dop']["HDOP"])
                day[constellation]["metadata"]["vdop"].append(result['dop']["VDOP"])
                for clave, valor in result['geometry'].items():
                    day[constellation]["metadata"][clave].append(valor)

    
    for constellation in day.keys():
        if day[constellation]["positions"]:
            day[constellation]["positions"] = np.array(day[constellation]["positions"])
    
    return day
