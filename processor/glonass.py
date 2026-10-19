from CONSTANTS import GNSS_CONSTANTS
import numpy as np 
import pandas as pd

GLONASS_CONSTANTS = GNSS_CONSTANTS['R'] 

def glonass_equations_of_motion(state, ax_ls, ay_ls, az_ls):
    """
    Equations of motion in the ECEF frame (PZ-90).
    INCLUDES Coriolis and centrifugal forces.
    """
    mu = GLONASS_CONSTANTS['mu']
    omega_e = GLONASS_CONSTANTS['omega_e']
    ae = GLONASS_CONSTANTS['ae']
    J2 = GLONASS_CONSTANTS['J2']
    
    x, y, z, vx, vy, vz = state
    
    r = np.sqrt(x**2 + y**2 + z**2)
    r2 = r**2
    r5 = r**5
    
    # --- 1. Gravitational part (central + J2) ---
    c1 = 1.5 * J2 * mu * (ae**2) / r5
    z2_r2 = (z**2) / r2
    
    # Pure gravitational acceleration
    ax_grav = -mu * x / (r**3) + c1 * x * (1.0 - 5.0 * z2_r2)
    ay_grav = -mu * y / (r**3) + c1 * y * (1.0 - 5.0 * z2_r2)
    az_grav = -mu * z / (r**3) + c1 * z * (3.0 - 5.0 * z2_r2)
    
    # --- 2. Rotational part ---
    # Coriolis: 2 * omega x v
    # Centrifugal: omega x (omega x r) -> simplifies to omega^2 * r_xy
    
    ax_rot = (omega_e**2 * x) + (2.0 * omega_e * vy)
    ay_rot = (omega_e**2 * y) - (2.0 * omega_e * vx)
    az_rot = 0.0 # Earth rotation induces no force along Z (rotation axis)
    
    # --- 3. Total ---
    ax_total = ax_grav + ax_ls + ax_rot
    ay_total = ay_grav + ay_ls + ay_rot
    az_total = az_grav + az_ls + az_rot
    
    return np.array([vx, vy, vz, ax_total, ay_total, az_total])

def runge_kutta_4_step(state, h, acc_ls):
    ax, ay, az = acc_ls
    k1 = glonass_equations_of_motion(state, ax, ay, az)
    k2 = glonass_equations_of_motion(state + 0.5 * h * k1, ax, ay, az)
    k3 = glonass_equations_of_motion(state + 0.5 * h * k2, ax, ay, az)
    k4 = glonass_equations_of_motion(state + h * k3, ax, ay, az)
    return state + (h / 6.0) * (k1 + 2*k2 + 2*k3 + k4)

def integrate_glonass_orbit(state0, dt, acc_ls):
    """
    Integrates the orbit making sure the target time is reached exactly.
    """
    if abs(dt) < 1e-9:
        return state0
        
    state = state0.copy()
    step_size = 30.0 # 30 s is a good accuracy/speed compromise
    
    # Step sign
    h = step_size if dt > 0 else -step_size
    
    # Accumulated time
    t_accum = 0.0
    
    # Main loop
    while abs(dt - t_accum) > 1e-9:
        remaining = dt - t_accum
        
        # If the remainder is shorter than the step, take an exact step
        if abs(remaining) < abs(h):
            step_current = remaining
        else:
            step_current = h
            
        state = runge_kutta_4_step(state, step_current, acc_ls)
        t_accum += step_current
        
    return state

def calculate_glonass_position(eph, obs_seconds):
    # 1. Extraction and unit conversion (km -> m)
    # GLONASS ephemerides come in km and km/s
    x0 = float(eph['X']) * 1000.0
    y0 = float(eph['Y']) * 1000.0
    z0 = float(eph['Z']) * 1000.0
    
    vx0 = float(eph['dX']) * 1000.0
    vy0 = float(eph['dY']) * 1000.0
    vz0 = float(eph['dZ']) * 1000.0
    
    # Lunisolar accelerations also come in km/s^2
    ax_ls = float(eph['dX2']) * 1000.0
    ay_ls = float(eph['dY2']) * 1000.0
    az_ls = float(eph['dZ2']) * 1000.0
    
    # 2. Time handling
    # Assumes eph['MessageFrameTime'] is on the same time scale as obs_seconds.
    # Beware of the 18 s GPS-UTC offset if obs_seconds is GPS time and the
    # ephemeris is in UTC.
    
    t_oe = float(eph['MessageFrameTime']) 
    
    # If the time scales are not aligned, dt will be huge.
    # dt should ideally lie between -900 and +900 s (15 min)
    dt = obs_seconds - t_oe
    
    # Week rollover correction if needed
    if dt > 302400: dt -= 604800
    if dt < -302400: dt += 604800
    
    # 3. Integration
    state0 = np.array([x0, y0, z0, vx0, vy0, vz0])
    acc_ls = (ax_ls, ay_ls, az_ls)
    
    final_state = integrate_glonass_orbit(state0, dt, acc_ls)
    
    # 4. Result (metres, PZ-90 frame)
    x, y, z = final_state[0], final_state[1], final_state[2]
    
    # A conversion to WGS84 would go here if needed, although PZ-90.11 and
    # WGS84 differ by centimetres.
    
    # 5. Clock
    tau_n = float(eph['SVclockBias']) if 'SVclockBias' in eph else 0.0
    gamma_n = float(eph['SVrelFreqBias']) if 'SVrelFreqBias' in eph else 0.0
    
    # Relativistic correction (optional)
    # dTr = -2 * (x*vx + y*vy + z*vz) / c^2
    
    clk_corr = -tau_n + gamma_n * dt
    
    return x, y, z, clk_corr, eph['MessageFrameTime'], eph['FreqNum']


def calculate_glonass_position(eph: pd.DataFrame, obs_seconds: int) -> tuple[np.float64, np.float64, np.float64, np.float64, np.float64, int]:
    # 1. Extraction and unit conversion (km -> m)
    # GLONASS ephemerides come in km and km/s
    x0 = float(eph['X'])
    y0 = float(eph['Y'])
    z0 = float(eph['Z'])
    
    vx0 = float(eph['dX'])
    vy0 = float(eph['dY'])
    vz0 = float(eph['dZ'])
    
    # Lunisolar accelerations also come in km/s^2
    ax_ls = float(eph['dX2'])
    ay_ls = float(eph['dY2'])
    az_ls = float(eph['dZ2'])
    
    # 2. Time handling
    # Assumes eph['MessageFrameTime'] is on the same time scale as obs_seconds.
    # Beware of the 18 s GPS-UTC offset if obs_seconds is GPS time and the
    # ephemeris is in UTC.
    
    t_oe = float(eph['MessageFrameTime']) 
    
    # If the time scales are not aligned, dt will be huge.
    # dt should ideally lie between -900 and +900 s (15 min)
    dt = float(int(obs_seconds+1)) - t_oe
    
    # Week rollover correction if needed
    if dt > 302400: dt -= 604800
    if dt < -302400: dt += 604800
    
    # 3. Integration
    state0 = np.array([x0, y0, z0, vx0, vy0, vz0])
    acc_ls = (ax_ls, ay_ls, az_ls)
    
    final_state = integrate_glonass_orbit(state0, dt, acc_ls)
    
    # 4. Result (metres, PZ-90 frame)
    x, y, z = final_state[0], final_state[1], final_state[2]
    
    # A conversion to WGS84 would go here if needed, although PZ-90.11 and
    # WGS84 differ by centimetres.
    
    # 5. Clock
    tau_n = float(eph['SVclockBias']) if 'SVclockBias' in eph else 0.0
    gamma_n = float(eph['SVrelFreqBias']) if 'SVrelFreqBias' in eph else 0.0
    
    # Relativistic correction (optional)
    # dTr = -2 * (x*vx + y*vy + z*vz) / c^2
    
    clk_corr = tau_n + gamma_n * dt
    
    return x, y, z, clk_corr, eph['MessageFrameTime'], eph['FreqNum']
