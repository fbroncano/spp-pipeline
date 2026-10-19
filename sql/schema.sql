-- Schema of the SPP results database used in this work.
-- It extends the GeoGNSS-PS schema (Broncano et al., Electronics 15(17), 3899, 2026,
-- doi:10.3390/electronics15173899) with additional per-session quality statistics
-- (HDOP, VDOP, 95th percentiles, elevation, azimuth and per-system satellite counts)
-- and with insert functions that record the frame and epoch of station coordinates.
CREATE EXTENSION IF NOT EXISTS postgis;
-- Functions are created before the tables they query, as pg_dump orders them.
SET check_function_bodies = false;

-- Name: public; Type: SCHEMA; Schema: -; Owner: -


-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -


-- Name: enu_offset; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.enu_offset AS (
	east double precision,
	north double precision,
	up double precision
);

-- Name: ecef_to_enu(public.geometry, public.geometry); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.ecef_to_enu(p_position public.geometry, p_reference public.geometry) RETURNS public.enu_offset
    LANGUAGE sql STABLE
    AS $$
    SELECT ROW(
        -sin(lon) * dx + cos(lon) * dy,
        -sin(lat) * cos(lon) * dx - sin(lat) * sin(lon) * dy + cos(lat) * dz,
         cos(lat) * cos(lon) * dx + cos(lat) * sin(lon) * dy + sin(lat) * dz
    )::enu_offset
    FROM (
        SELECT ST_X(p_position) - ST_X(p_reference) AS dx,
               ST_Y(p_position) - ST_Y(p_reference) AS dy,
               ST_Z(p_position) - ST_Z(p_reference) AS dz,
               radians(ST_Y(ST_Transform(p_reference, 4326))) AS lat,
               radians(ST_X(ST_Transform(p_reference, 4326))) AS lon
    ) t;
$$;

-- Name: ecef_to_enu(public.geometry, public.geometry, double precision, double precision); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.ecef_to_enu(p_position public.geometry, p_reference public.geometry, p_lat double precision, p_lon double precision) RETURNS public.enu_offset
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT ROW(
        -sin(p_lon) * dx + cos(p_lon) * dy,
        -sin(p_lat) * cos(p_lon) * dx - sin(p_lat) * sin(p_lon) * dy + cos(p_lat) * dz,
         cos(p_lat) * cos(p_lon) * dx + cos(p_lat) * sin(p_lon) * dy + sin(p_lat) * dz
    )::enu_offset
    FROM (
        SELECT ST_X(p_position) - ST_X(p_reference) AS dx,
               ST_Y(p_position) - ST_Y(p_reference) AS dy,
               ST_Z(p_position) - ST_Z(p_reference) AS dz
    ) t;
$$;

-- Name: enu_to_ecef(double precision, double precision, double precision, public.geometry); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.enu_to_ecef(p_east double precision, p_north double precision, p_up double precision, p_reference public.geometry) RETURNS public.geometry
    LANGUAGE sql STABLE
    AS $$
    SELECT ST_SetSRID(ST_MakePoint(
        ST_X(p_reference) + (-sin(lon) * p_east - sin(lat) * cos(lon) * p_north
                             + cos(lat) * cos(lon) * p_up),
        ST_Y(p_reference) + ( cos(lon) * p_east - sin(lat) * sin(lon) * p_north
                             + cos(lat) * sin(lon) * p_up),
        ST_Z(p_reference) + ( cos(lat) * p_north + sin(lat) * p_up)
    ), 4978)
    FROM (
        SELECT radians(ST_Y(ST_Transform(p_reference, 4326))) AS lat,
               radians(ST_X(ST_Transform(p_reference, 4326))) AS lon
    ) t;
$$;

-- Name: get_constellation_stats(character, smallint); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.get_constellation_stats(p_station_id character, p_method_id smallint DEFAULT 5) RETURNS TABLE(constellation_name character varying, n_days bigint, avg_error_3d double precision, avg_n_satellites numeric, avg_pdop numeric, avg_rms numeric)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_ref_x DOUBLE PRECISION;
    v_ref_y DOUBLE PRECISION;
    v_ref_z DOUBLE PRECISION;
BEGIN
    SELECT ST_X(s.location), ST_Y(s.location), ST_Z(s.location)
    INTO v_ref_x, v_ref_y, v_ref_z
    FROM stations s
    WHERE s.id = p_station_id;

    RETURN QUERY
    SELECT
        (SELECT c.name FROM constellations c WHERE c.id = dp.constellation_id),
        COUNT(*),
        AVG(SQRT(
            POWER(ST_X(dp.position) - v_ref_x, 2) +
            POWER(ST_Y(dp.position) - v_ref_y, 2) +
            POWER(ST_Z(dp.position) - v_ref_z, 2)
        )),
        AVG(ds.n_satellites_avg),
        AVG(ds.pdop_avg),
        AVG(ds.rms_avg)
    FROM daily_positions dp
    JOIN daily_statistics ds ON dp.station_id = ds.station_id
        AND dp.date = ds.date
        AND dp.constellation_id = ds.constellation_id
    WHERE dp.station_id = p_station_id AND dp.method_id = p_method_id
    GROUP BY dp.constellation_id
    ORDER BY dp.constellation_id;
END;
$$;

-- Name: get_daily_positions(character, date); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.get_daily_positions(p_station_id character, p_date date) RETURNS TABLE(constellation_code character varying, constellation_name character varying, method character varying, x double precision, y double precision, z double precision, std_3d numeric, n_epochs_used integer, n_epochs integer, run_id integer)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT c.code FROM constellations c WHERE c.id = dp.constellation_id),
        (SELECT c.name FROM constellations c WHERE c.id = dp.constellation_id),
        (SELECT m.code FROM methods m WHERE m.id = dp.method_id),
        ST_X(dp.position),
        ST_Y(dp.position),
        ST_Z(dp.position),
        dp.std_3d,
        dp.n_epochs_used,
        dp.n_epochs,
        dp.run_id
    FROM daily_positions dp
    WHERE dp.station_id = p_station_id AND dp.date = p_date
    ORDER BY dp.constellation_id, dp.method_id;
END;
$$;

-- Name: get_daily_statistics(character, date); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.get_daily_statistics(p_station_id character, p_date date) RETURNS TABLE(constellation_code character varying, constellation_name character varying, n_satellites_avg numeric, clock_bias_m numeric, gdop_avg numeric, pdop_avg numeric, pdop_max numeric, tdop_avg numeric, rms_avg numeric, rms_max numeric, run_id integer)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT c.code FROM constellations c WHERE c.id = ds.constellation_id),
        (SELECT c.name FROM constellations c WHERE c.id = ds.constellation_id),
        ds.n_satellites_avg,
        ds.clock_bias_m,
        ds.gdop_avg,
        ds.pdop_avg,
        ds.pdop_max,
        ds.tdop_avg,
        ds.rms_avg,
        ds.rms_max,
        ds.run_id
    FROM daily_statistics ds
    WHERE ds.station_id = p_station_id AND ds.date = p_date
    ORDER BY ds.constellation_id;
END;
$$;

-- Name: get_position_errors(character, date); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.get_position_errors(p_station_id character, p_date date) RETURNS TABLE(constellation_code character varying, method character varying, error_x double precision, error_y double precision, error_z double precision, error_3d double precision)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_ref_x DOUBLE PRECISION;
    v_ref_y DOUBLE PRECISION;
    v_ref_z DOUBLE PRECISION;
BEGIN
    SELECT ST_X(s.location), ST_Y(s.location), ST_Z(s.location)
    INTO v_ref_x, v_ref_y, v_ref_z
    FROM stations s
    WHERE s.id = p_station_id;

    RETURN QUERY
    SELECT
        (SELECT c.code FROM constellations c WHERE c.id = dp.constellation_id),
        (SELECT m.code FROM methods m WHERE m.id = dp.method_id),
        ST_X(dp.position) - v_ref_x,
        ST_Y(dp.position) - v_ref_y,
        ST_Z(dp.position) - v_ref_z,
        SQRT(
            POWER(ST_X(dp.position) - v_ref_x, 2) +
            POWER(ST_Y(dp.position) - v_ref_y, 2) +
            POWER(ST_Z(dp.position) - v_ref_z, 2)
        )
    FROM daily_positions dp
    WHERE dp.station_id = p_station_id AND dp.date = p_date
    ORDER BY dp.constellation_id, dp.method_id;
END;
$$;

-- Name: get_station(character); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.get_station(p_id character) RETURNS TABLE(station_id character, name character varying, city character varying, province character varying, initial_date date, x double precision, y double precision, z double precision, frame_id smallint, coord_epoch numeric)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.id,
        s.name,
        s.city,
        s.province,
        s.initial_date,
        ST_X(s.location),
        ST_Y(s.location),
        ST_Z(s.location),
        s.frame_id,
        s.coord_epoch
    FROM stations s
    WHERE s.id = p_id;
END;
$$;

-- Name: get_station_equipment(character, date); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.get_station_equipment(p_station_id character, p_date date) RETURNS TABLE(delta_h numeric, delta_n numeric, delta_e numeric, antenna_type character varying, antenna_serial character varying, receiver_type character varying, firmware character varying)
    LANGUAGE sql STABLE
    AS $$
    SELECT a.delta_h, a.delta_n, a.delta_e,
           a.antenna_type, a.antenna_serial,
           r.receiver_type, r.firmware
    FROM station_antenna a
    FULL JOIN station_receiver r
           ON r.station_id = a.station_id
          AND p_date >= r.valid_from::date
          AND (r.valid_to IS NULL OR p_date < r.valid_to::date)
    WHERE a.station_id = p_station_id
      AND p_date >= a.valid_from::date
      AND (a.valid_to IS NULL OR p_date < a.valid_to::date);
$$;

-- Name: insert_daily_position(character, date, smallint, smallint, numeric, numeric, numeric, numeric, numeric, numeric, numeric, integer, integer, integer); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.insert_daily_position(p_station_id character, p_date date, p_constellation_id smallint, p_method_id smallint, p_x numeric, p_y numeric, p_z numeric, p_std_x numeric, p_std_y numeric, p_std_z numeric, p_std_3d numeric, p_n_epochs_used integer, p_n_epochs integer, p_run_id integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_id INTEGER;
BEGIN
    INSERT INTO daily_positions (
        station_id, date, constellation_id, method_id,
        position, std_x, std_y, std_z, std_3d,
        n_epochs_used, n_epochs, run_id
    )
    VALUES (
        p_station_id, p_date, p_constellation_id, p_method_id,
        ST_SetSRID(ST_MakePoint(p_x, p_y, p_z), 4978),
        p_std_x, p_std_y, p_std_z, p_std_3d,
        p_n_epochs_used, p_n_epochs, p_run_id
    )
    ON CONFLICT (station_id, date, constellation_id, method_id)
    DO UPDATE SET
        position = EXCLUDED.position,
        std_x = EXCLUDED.std_x,
        std_y = EXCLUDED.std_y,
        std_z = EXCLUDED.std_z,
        std_3d = EXCLUDED.std_3d,
        n_epochs_used = EXCLUDED.n_epochs_used,
        n_epochs = EXCLUDED.n_epochs,
        run_id = EXCLUDED.run_id
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

-- Name: insert_daily_statistics(character, date, smallint, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, numeric, integer); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.insert_daily_statistics(p_station_id character, p_date date, p_constellation_id smallint, p_n_satellites_avg numeric, p_clock_bias_m numeric, p_gdop_avg numeric, p_gdop_p95 numeric, p_gdop_max numeric, p_pdop_avg numeric, p_pdop_p95 numeric, p_pdop_max numeric, p_tdop_avg numeric, p_tdop_p95 numeric, p_tdop_max numeric, p_hdop_avg numeric, p_hdop_p95 numeric, p_hdop_max numeric, p_vdop_avg numeric, p_vdop_p95 numeric, p_vdop_max numeric, p_rms_avg numeric, p_rms_p95 numeric, p_rms_max numeric, p_elev_avg numeric, p_elev_min numeric, p_n_sat_low_avg numeric, p_azim_resultant numeric, p_azim_direction numeric, p_n_sat_g_avg numeric, p_n_sat_e_avg numeric, p_n_sat_c_avg numeric, p_run_id integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_id INTEGER;
BEGIN
    INSERT INTO daily_statistics (
        station_id, date, constellation_id,
        n_satellites_avg, clock_bias_m,
        gdop_avg, gdop_p95, gdop_max,
        pdop_avg, pdop_p95, pdop_max,
        tdop_avg, tdop_p95, tdop_max,
        hdop_avg, hdop_p95, hdop_max,
        vdop_avg, vdop_p95, vdop_max,
        rms_avg, rms_p95, rms_max,
        elev_avg, elev_min, n_sat_low_avg,
        azim_resultant, azim_direction,
        n_sat_g_avg, n_sat_e_avg, n_sat_c_avg,
        run_id
    )
    VALUES (
        p_station_id, p_date, p_constellation_id,
        p_n_satellites_avg, p_clock_bias_m,
        p_gdop_avg, p_gdop_p95, p_gdop_max,
        p_pdop_avg, p_pdop_p95, p_pdop_max,
        p_tdop_avg, p_tdop_p95, p_tdop_max,
        p_hdop_avg, p_hdop_p95, p_hdop_max,
        p_vdop_avg, p_vdop_p95, p_vdop_max,
        p_rms_avg, p_rms_p95, p_rms_max,
        p_elev_avg, p_elev_min, p_n_sat_low_avg,
        p_azim_resultant, p_azim_direction,
        p_n_sat_g_avg, p_n_sat_e_avg, p_n_sat_c_avg,
        p_run_id
    )
    ON CONFLICT (station_id, date, constellation_id)
    DO UPDATE SET
        n_satellites_avg = EXCLUDED.n_satellites_avg,
        clock_bias_m     = EXCLUDED.clock_bias_m,
        gdop_avg = EXCLUDED.gdop_avg, gdop_p95 = EXCLUDED.gdop_p95, gdop_max = EXCLUDED.gdop_max,
        pdop_avg = EXCLUDED.pdop_avg, pdop_p95 = EXCLUDED.pdop_p95, pdop_max = EXCLUDED.pdop_max,
        tdop_avg = EXCLUDED.tdop_avg, tdop_p95 = EXCLUDED.tdop_p95, tdop_max = EXCLUDED.tdop_max,
        hdop_avg = EXCLUDED.hdop_avg, hdop_p95 = EXCLUDED.hdop_p95, hdop_max = EXCLUDED.hdop_max,
        vdop_avg = EXCLUDED.vdop_avg, vdop_p95 = EXCLUDED.vdop_p95, vdop_max = EXCLUDED.vdop_max,
        rms_avg  = EXCLUDED.rms_avg,  rms_p95  = EXCLUDED.rms_p95,  rms_max  = EXCLUDED.rms_max,
        elev_avg       = EXCLUDED.elev_avg,
        elev_min       = EXCLUDED.elev_min,
        n_sat_low_avg  = EXCLUDED.n_sat_low_avg,
        azim_resultant = EXCLUDED.azim_resultant,
        azim_direction = EXCLUDED.azim_direction,
        n_sat_g_avg    = EXCLUDED.n_sat_g_avg,
        n_sat_e_avg    = EXCLUDED.n_sat_e_avg,
        n_sat_c_avg    = EXCLUDED.n_sat_c_avg,
        run_id         = EXCLUDED.run_id
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

-- Name: insert_station(character, character varying, character varying, character varying, date, double precision, double precision, double precision, smallint, numeric); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.insert_station(p_id character, p_name character varying, p_city character varying, p_province character varying, p_initial_date date, p_x double precision, p_y double precision, p_z double precision, p_frame_id smallint DEFAULT NULL::smallint, p_coord_epoch numeric DEFAULT NULL::numeric) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO stations (id, name, city, province, initial_date,
                          location, frame_id, coord_epoch)
    VALUES (p_id, p_name, p_city, p_province, p_initial_date,
            ST_SetSRID(ST_MakePoint(p_x, p_y, p_z), 4978),
            p_frame_id, p_coord_epoch)
    ON CONFLICT (id) DO UPDATE SET
        name         = EXCLUDED.name,
        city         = EXCLUDED.city,
        province     = EXCLUDED.province,
        initial_date = EXCLUDED.initial_date,
        location     = EXCLUDED.location,
        frame_id     = EXCLUDED.frame_id,
        coord_epoch  = EXCLUDED.coord_epoch;
END;
$$;

-- Name: nearest_positions_nd(double precision, double precision, double precision, integer); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.nearest_positions_nd(p_x double precision, p_y double precision, p_z double precision, p_limit integer DEFAULT 5) RETURNS TABLE(station_code character, session_date date, distance_3d double precision)
    LANGUAGE sql STABLE
    AS $$
    SELECT dp.station_id,
           dp.date,
           ST_3DDistance(dp.position,
                         ST_SetSRID(ST_MakePoint(p_x, p_y, p_z), 4978))
    FROM   daily_positions dp
    ORDER  BY dp.position <<->> ST_SetSRID(ST_MakePoint(p_x, p_y, p_z), 4978)
    LIMIT  p_limit;
$$;

-- Name: positions_in_envelope_2d(double precision, double precision, double precision, double precision); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.positions_in_envelope_2d(p_xmin double precision, p_ymin double precision, p_xmax double precision, p_ymax double precision) RETURNS bigint
    LANGUAGE sql STABLE
    AS $$
    SELECT COUNT(*)
    FROM   daily_positions dp
    WHERE  dp.position && ST_MakeEnvelope(p_xmin, p_ymin, p_xmax, p_ymax, 4978);
$$;

-- Name: positions_in_envelope_nd(double precision, double precision, double precision, double precision, double precision, double precision); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.positions_in_envelope_nd(p_xmin double precision, p_ymin double precision, p_zmin double precision, p_xmax double precision, p_ymax double precision, p_zmax double precision) RETURNS bigint
    LANGUAGE sql STABLE
    AS $$
    SELECT COUNT(*)
    FROM   daily_positions dp
    WHERE  dp.position &&& ST_3DMakeBox(
               ST_SetSRID(ST_MakePoint(p_xmin, p_ymin, p_zmin), 4978),
               ST_SetSRID(ST_MakePoint(p_xmax, p_ymax, p_zmax), 4978));
$$;

-- Name: quality_filtered_positions(smallint, smallint, date, date, numeric, numeric); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.quality_filtered_positions(p_constellation_id smallint, p_method_id smallint, p_date_from date, p_date_to date, p_std_3d_max numeric, p_pdop_max numeric) RETURNS TABLE(station_code character, n_total bigint, n_kept bigint, bias_all numeric, bias_kept numeric, mean_std_3d numeric, mean_pdop numeric)
    LANGUAGE sql STABLE
    AS $$
    WITH joined AS (
        SELECT dp.station_id,
               dp.position,
               dp.std_3d,
               ds.pdop_avg,
               (dp.std_3d <= p_std_3d_max AND ds.pdop_avg <= p_pdop_max) AS keep
        FROM   daily_positions  dp
        JOIN   daily_statistics ds
               ON  ds.station_id       = dp.station_id
               AND ds.date             = dp.date
               AND ds.constellation_id = dp.constellation_id
        WHERE  dp.constellation_id = p_constellation_id
          AND  dp.method_id        = p_method_id
          AND  dp.date BETWEEN p_date_from AND p_date_to
    ),
    agg AS (
        SELECT j.station_id,
               COUNT(*)                            AS n_total,
               COUNT(*) FILTER (WHERE j.keep)      AS n_kept,
               ST_GeometricMedian(ST_Collect(j.position))                       AS p_all,
               ST_GeometricMedian(ST_Collect(j.position) FILTER (WHERE j.keep)) AS p_kept,
               AVG(j.std_3d)                       AS mean_std_3d,
               AVG(j.pdop_avg)                     AS mean_pdop
        FROM   joined j
        GROUP  BY j.station_id
    )
    SELECT a.station_id,
           a.n_total,
           a.n_kept,
           ST_3DDistance(a.p_all,  station_reference(a.station_id, p_date_to))::NUMERIC(10,3),
           ST_3DDistance(a.p_kept, station_reference(a.station_id, p_date_to))::NUMERIC(10,3),
           a.mean_std_3d::NUMERIC(10,2),
           a.mean_pdop::NUMERIC(10,2)
    FROM   agg a
    ORDER  BY a.station_id;
$$;

-- Name: representative_enu(smallint, smallint, date, date); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.representative_enu(p_constellation_id smallint, p_method_id smallint, p_date_from date, p_date_to date) RETURNS TABLE(station_code character, n_positions bigint, east numeric, north numeric, up numeric, horizontal numeric)
    LANGUAGE sql STABLE
    AS $$
    WITH agg AS (
        SELECT dp.station_id,
               COUNT(*)                                    AS n,
               ST_GeometricMedian(ST_Collect(dp.position))  AS p_med
        FROM   daily_positions dp
        WHERE  dp.constellation_id = p_constellation_id
          AND  dp.method_id        = p_method_id
          AND  dp.date BETWEEN p_date_from AND p_date_to
        GROUP  BY dp.station_id
    ),
    -- LATERAL, y no (ecef_to_enu(...)).*, porque la seleccion de campo sobre el
    -- resultado de una funcion la evalua una vez por cada campo pedido. Con
    -- tres componentes eso triplica el coste, y en un barrido fila a fila la
    -- diferencia llega a ser de un orden de magnitud.
    enu AS (
        SELECT a.station_id, a.n, e.east, e.north, e.up
        FROM   agg a,
        LATERAL ecef_to_enu(a.p_med,
                            station_reference(a.station_id, p_date_to)) AS e
    )
    SELECT station_id,
           n,
           east::NUMERIC(10,3),
           north::NUMERIC(10,3),
           up::NUMERIC(10,3),
           sqrt(pow(east, 2) + pow(north, 2))::NUMERIC(10,3)
    FROM   enu
    ORDER  BY station_id;
$$;

-- Name: representative_positions(smallint, smallint, date, date); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.representative_positions(p_constellation_id smallint, p_method_id smallint, p_date_from date, p_date_to date) RETURNS TABLE(station_code character, n_positions bigint, med_x numeric, med_y numeric, med_z numeric, bias_3d numeric, mean_daily_error numeric, mean_std_3d numeric)
    LANGUAGE sql STABLE
    AS $$
    -- The antenna-corrected reference is resolved once per station, for the
    -- configuration in force at the end of the interval, instead of once per
    -- row. Mixing references within a single series would otherwise introduce
    -- a step at every antenna change.
    WITH ref AS (
        SELECT s.id AS station_id,
               station_reference(s.id, p_date_to) AS apc
        FROM   stations s
    ),
    agg AS (
        -- ST_GeometricMedian is evaluated once per station here; calling it
        -- separately for X, Y and Z would recompute the whole estimate.
        SELECT dp.station_id,
               COUNT(*)                                   AS n,
               ST_GeometricMedian(ST_Collect(dp.position)) AS p_med,
               AVG(ST_3DDistance(dp.position, r.apc))      AS mean_err,
               AVG(dp.std_3d)                              AS mean_std
        FROM   daily_positions dp
        JOIN   ref r ON r.station_id = dp.station_id
        WHERE  dp.constellation_id = p_constellation_id
          AND  dp.method_id        = p_method_id
          AND  dp.date BETWEEN p_date_from AND p_date_to
        GROUP  BY dp.station_id
    )
    SELECT a.station_id,
           a.n,
           ST_X(a.p_med)::NUMERIC(12,3),
           ST_Y(a.p_med)::NUMERIC(12,3),
           ST_Z(a.p_med)::NUMERIC(12,3),
           ST_3DDistance(a.p_med, r.apc)::NUMERIC(10,3),
           a.mean_err::NUMERIC(10,3),
           a.mean_std::NUMERIC(10,3)
    FROM   agg a
    JOIN   ref r ON r.station_id = a.station_id
    ORDER  BY a.station_id;
$$;

-- Name: rms_outlier_sessions(numeric); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.rms_outlier_sessions(p_sigmas numeric DEFAULT 3) RETURNS TABLE(station_code character, session_date date, constellation_id smallint, rms_max numeric, rms_median numeric, rms_sigma numeric)
    LANGUAGE sql STABLE
    AS $$
    WITH med AS (
        SELECT ds.station_id,
               ds.constellation_id,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY ds.rms_max) AS rms_med
        FROM   daily_statistics ds
        GROUP  BY ds.station_id, ds.constellation_id
    ),
    mad AS (
        SELECT ds.station_id,
               ds.constellation_id,
               1.4826 * percentile_cont(0.5)
                        WITHIN GROUP (ORDER BY abs(ds.rms_max - med.rms_med))
                        AS rms_sigma
        FROM   daily_statistics ds
        JOIN   med USING (station_id, constellation_id)
        GROUP  BY ds.station_id, ds.constellation_id
    )
    SELECT ds.station_id,
           ds.date,
           ds.constellation_id,
           ds.rms_max::NUMERIC(10,3),
           med.rms_med::NUMERIC(10,3),
           mad.rms_sigma::NUMERIC(10,3)
    FROM   daily_statistics ds
    JOIN   med USING (station_id, constellation_id)
    JOIN   mad USING (station_id, constellation_id)
    WHERE  mad.rms_sigma > 0
      AND  ds.rms_max - med.rms_med > p_sigmas * mad.rms_sigma
    ORDER  BY ds.station_id, ds.constellation_id, ds.date;
$$;

-- Name: station_reference(character, date); Type: FUNCTION; Schema: public; Owner: -

CREATE FUNCTION public.station_reference(p_station_id character, p_date date) RETURNS public.geometry
    LANGUAGE sql STABLE
    AS $$
    SELECT COALESCE(
        (SELECT enu_to_ecef(a.delta_e::DOUBLE PRECISION,
                            a.delta_n::DOUBLE PRECISION,
                            a.delta_h::DOUBLE PRECISION,
                            s.location)
         FROM   station_antenna a
         WHERE  a.station_id = s.id
           AND  p_date >= a.valid_from::DATE
           AND  (a.valid_to IS NULL OR p_date < a.valid_to::DATE)
         ORDER  BY a.valid_from DESC
         LIMIT  1),
        s.location)
    FROM stations s
    WHERE s.id = p_station_id;
$$;

-- Name: constellations; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.constellations (
    id smallint NOT NULL,
    code character varying(3) NOT NULL,
    name character varying(20) NOT NULL
);

-- Name: daily_positions; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.daily_positions (
    id integer NOT NULL,
    station_id character(4) NOT NULL,
    date date NOT NULL,
    constellation_id smallint NOT NULL,
    method_id smallint NOT NULL,
    run_id integer NOT NULL,
    "position" public.geometry(PointZ,4978) NOT NULL,
    std_x numeric,
    std_y numeric,
    std_z numeric,
    std_3d numeric,
    n_epochs_used integer,
    n_epochs integer
);

-- Name: daily_positions_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.daily_positions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- Name: daily_positions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.daily_positions_id_seq OWNED BY public.daily_positions.id;

-- Name: daily_statistics; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.daily_statistics (
    id integer NOT NULL,
    station_id character(4) NOT NULL,
    date date NOT NULL,
    constellation_id smallint NOT NULL,
    run_id integer NOT NULL,
    n_satellites_avg numeric,
    clock_bias_m numeric,
    gdop_avg numeric,
    pdop_avg numeric,
    pdop_max numeric,
    tdop_avg numeric,
    rms_avg numeric,
    rms_max numeric,
    hdop_avg numeric,
    hdop_p95 numeric,
    hdop_max numeric,
    vdop_avg numeric,
    vdop_p95 numeric,
    vdop_max numeric,
    gdop_p95 numeric,
    gdop_max numeric,
    pdop_p95 numeric,
    tdop_p95 numeric,
    tdop_max numeric,
    rms_p95 numeric,
    elev_avg numeric,
    elev_min numeric,
    n_sat_low_avg numeric,
    azim_resultant numeric,
    azim_direction numeric,
    n_sat_g_avg numeric,
    n_sat_e_avg numeric,
    n_sat_c_avg numeric
);

-- Name: daily_statistics_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.daily_statistics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- Name: daily_statistics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.daily_statistics_id_seq OWNED BY public.daily_statistics.id;

-- Name: ephemeris_type; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.ephemeris_type (
    ep_id smallint NOT NULL,
    ep_description character varying NOT NULL
);

-- Name: methods; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.methods (
    id smallint NOT NULL,
    code character varying(15) NOT NULL,
    description character varying(50)
);

-- Name: reference_frames; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.reference_frames (
    frame_id smallint NOT NULL,
    code character varying(16) NOT NULL,
    name character varying(60) NOT NULL
);

-- Name: runs; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.runs (
    run_id integer NOT NULL,
    sw_id smallint NOT NULL,
    ep_id smallint NOT NULL,
    frame_id smallint,
    decimation_s integer NOT NULL,
    "time" timestamp with time zone DEFAULT now() NOT NULL
);

-- Name: runs_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.runs_run_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- Name: runs_run_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.runs_run_id_seq OWNED BY public.runs.run_id;

-- Name: software; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.software (
    sw_id smallint NOT NULL,
    sw_name character varying NOT NULL,
    sw_version character varying NOT NULL
);

-- Name: station_antenna; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.station_antenna (
    id integer NOT NULL,
    station_id character(4) NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    antenna_type character varying(20),
    antenna_serial character varying(30),
    radome character varying(10),
    delta_h numeric(8,4) NOT NULL,
    delta_n numeric(8,4) DEFAULT 0 NOT NULL,
    delta_e numeric(8,4) DEFAULT 0 NOT NULL
);

-- Name: station_antenna_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.station_antenna_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- Name: station_antenna_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.station_antenna_id_seq OWNED BY public.station_antenna.id;

-- Name: station_receiver; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.station_receiver (
    id integer NOT NULL,
    station_id character(4) NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    receiver_type character varying(30),
    firmware character varying(40)
);

-- Name: station_receiver_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.station_receiver_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- Name: station_receiver_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.station_receiver_id_seq OWNED BY public.station_receiver.id;

-- Name: stations; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.stations (
    id character(4) NOT NULL,
    name character varying(25),
    city character varying(27),
    province character varying(13),
    initial_date date,
    location public.geometry(PointZ,4978),
    frame_id smallint,
    coord_epoch numeric(7,2)
);

-- Name: daily_positions id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_positions ALTER COLUMN id SET DEFAULT nextval('public.daily_positions_id_seq'::regclass);

-- Name: daily_statistics id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_statistics ALTER COLUMN id SET DEFAULT nextval('public.daily_statistics_id_seq'::regclass);

-- Name: runs run_id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.runs ALTER COLUMN run_id SET DEFAULT nextval('public.runs_run_id_seq'::regclass);

-- Name: station_antenna id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.station_antenna ALTER COLUMN id SET DEFAULT nextval('public.station_antenna_id_seq'::regclass);

-- Name: station_receiver id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.station_receiver ALTER COLUMN id SET DEFAULT nextval('public.station_receiver_id_seq'::regclass);

-- Name: constellations constellations_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.constellations
    ADD CONSTRAINT constellations_pkey PRIMARY KEY (id);

-- Name: daily_positions daily_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_positions
    ADD CONSTRAINT daily_positions_pkey PRIMARY KEY (id);

-- Name: daily_positions daily_positions_station_id_date_constellation_id_method_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_positions
    ADD CONSTRAINT daily_positions_station_id_date_constellation_id_method_id_key UNIQUE (station_id, date, constellation_id, method_id);

-- Name: daily_statistics daily_statistics_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_statistics
    ADD CONSTRAINT daily_statistics_pkey PRIMARY KEY (id);

-- Name: daily_statistics daily_statistics_station_id_date_constellation_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_statistics
    ADD CONSTRAINT daily_statistics_station_id_date_constellation_id_key UNIQUE (station_id, date, constellation_id);

-- Name: ephemeris_type ephemeris_type_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.ephemeris_type
    ADD CONSTRAINT ephemeris_type_pkey PRIMARY KEY (ep_id);

-- Name: methods methods_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.methods
    ADD CONSTRAINT methods_pkey PRIMARY KEY (id);

-- Name: reference_frames reference_frames_code_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.reference_frames
    ADD CONSTRAINT reference_frames_code_key UNIQUE (code);

-- Name: reference_frames reference_frames_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.reference_frames
    ADD CONSTRAINT reference_frames_pkey PRIMARY KEY (frame_id);

-- Name: runs runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_pkey PRIMARY KEY (run_id);

-- Name: software software_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.software
    ADD CONSTRAINT software_pkey PRIMARY KEY (sw_id);

-- Name: software software_sw_name_sw_version_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.software
    ADD CONSTRAINT software_sw_name_sw_version_key UNIQUE (sw_name, sw_version);

-- Name: station_antenna station_antenna_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.station_antenna
    ADD CONSTRAINT station_antenna_pkey PRIMARY KEY (id);

-- Name: station_antenna station_antenna_station_id_valid_from_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.station_antenna
    ADD CONSTRAINT station_antenna_station_id_valid_from_key UNIQUE (station_id, valid_from);

-- Name: station_receiver station_receiver_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.station_receiver
    ADD CONSTRAINT station_receiver_pkey PRIMARY KEY (id);

-- Name: station_receiver station_receiver_station_id_valid_from_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.station_receiver
    ADD CONSTRAINT station_receiver_station_id_valid_from_key UNIQUE (station_id, valid_from);

-- Name: stations stations_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT stations_pkey PRIMARY KEY (id);

-- Name: idx_daily_pos_constellation; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_daily_pos_constellation ON public.daily_statistics USING btree (station_id);

-- Name: idx_daily_pos_date; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_daily_pos_date ON public.daily_positions USING btree (date);

-- Name: idx_daily_pos_geom; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_daily_pos_geom ON public.daily_positions USING gist ("position");

-- Name: idx_daily_pos_geom_nd; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_daily_pos_geom_nd ON public.daily_positions USING gist ("position" public.gist_geometry_ops_nd);

-- Name: idx_daily_pos_method; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_daily_pos_method ON public.daily_statistics USING btree (date);

-- Name: idx_daily_pos_station; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_daily_pos_station ON public.daily_positions USING btree (station_id);

-- Name: idx_station_antenna_lookup; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_station_antenna_lookup ON public.station_antenna USING btree (station_id, valid_from, valid_to);

-- Name: idx_station_receiver_lookup; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_station_receiver_lookup ON public.station_receiver USING btree (station_id, valid_from, valid_to);

-- Name: idx_stations_geom_nd; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stations_geom_nd ON public.stations USING gist (location public.gist_geometry_ops_nd);

-- Name: daily_positions daily_positions_constellation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_positions
    ADD CONSTRAINT daily_positions_constellation_id_fkey FOREIGN KEY (constellation_id) REFERENCES public.constellations(id);

-- Name: daily_positions daily_positions_method_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_positions
    ADD CONSTRAINT daily_positions_method_id_fkey FOREIGN KEY (method_id) REFERENCES public.methods(id);

-- Name: daily_positions daily_positions_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_positions
    ADD CONSTRAINT daily_positions_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(run_id);

-- Name: daily_positions daily_positions_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_positions
    ADD CONSTRAINT daily_positions_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE CASCADE;

-- Name: daily_statistics daily_statistics_constellation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_statistics
    ADD CONSTRAINT daily_statistics_constellation_id_fkey FOREIGN KEY (constellation_id) REFERENCES public.constellations(id);

-- Name: daily_statistics daily_statistics_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_statistics
    ADD CONSTRAINT daily_statistics_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(run_id);

-- Name: daily_statistics daily_statistics_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_statistics
    ADD CONSTRAINT daily_statistics_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE CASCADE;

-- Name: runs runs_ep_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_ep_id_fkey FOREIGN KEY (ep_id) REFERENCES public.ephemeris_type(ep_id);

-- Name: runs runs_frame_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_frame_id_fkey FOREIGN KEY (frame_id) REFERENCES public.reference_frames(frame_id);

-- Name: runs runs_sw_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_sw_id_fkey FOREIGN KEY (sw_id) REFERENCES public.software(sw_id);

-- Name: station_antenna station_antenna_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.station_antenna
    ADD CONSTRAINT station_antenna_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE CASCADE;

-- Name: station_receiver station_receiver_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.station_receiver
    ADD CONSTRAINT station_receiver_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE CASCADE;

-- Name: stations stations_frame_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT stations_frame_id_fkey FOREIGN KEY (frame_id) REFERENCES public.reference_frames(frame_id);

