class HatchFilter:
    """
    Hatch filter: smooths code pseudorange using carrier phase.

    The pipeline feeds it the ionosphere-free code and phase of each satellite
    (see pre_processor.get_ionofree_pseudorange_smoothed), keyed by the pair of
    observables used, so there is no ionospheric divergence to cancel and a
    slip on either frequency restarts a single filter.
    """

    def __init__(self, max_epochs=100, cycle_slip_threshold=5.0):
        self.max_epochs = max_epochs
        self.cycle_slip_threshold = cycle_slip_threshold
        self.filters = {}  # {(sv, key): state}

    def reset(self, sv, freq):
        """Resets the filter for one satellite and observable key."""
        key = (sv, freq)
        if key in self.filters:
            del self.filters[key]

    def update(self, sv, freq, P, L, wavelength):
        """
        Updates the filter and returns the smoothed pseudorange.

        Parameters:
        -----------
        sv : str
            Satellite identifier ('G01', 'E14', etc.)
        freq : str
            Observable key, e.g. 'C1C/C2W' for an ionosphere-free pair
        P : float
            Code pseudorange (metres)
        L : float
            Carrier phase, in cycles, or in metres with wavelength=1.0
        wavelength : float
            Wavelength (metres); 1.0 when L is already in metres

        Returns:
        --------
        float : Smoothed pseudorange (metres)
        """

        key = (sv, freq)
        L_m = L * wavelength  # Convert phase to metres

        # First epoch, or filter does not exist yet
        if key not in self.filters:
            self.filters[key] = {
                'P_smooth': P,
                'L_prev': L_m,
                'n': 1
            }
            return P

        state = self.filters[key]

        # Detect cycle slip
        dL = L_m - state['L_prev']
        dP_expected = dL  # Phase variation ≈ range variation
        dP_smooth = P - state['P_smooth']

        if abs(dP_smooth - dP_expected) > self.cycle_slip_threshold:
            # Cycle slip detected, restart
            self.filters[key] = {
                'P_smooth': P,
                'L_prev': L_m,
                'n': 1
            }
            return P

        # Increase counter (up to the maximum)
        n = min(state['n'] + 1, self.max_epochs)
        alpha = 1.0 / n

        # Hatch filter
        P_smooth = alpha * P + (1 - alpha) * (state['P_smooth'] + dL)

        # Update state
        self.filters[key] = {
            'P_smooth': P_smooth,
            'L_prev': L_m,
            'n': n
        }

        return P_smooth
