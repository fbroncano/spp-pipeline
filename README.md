# Multi-constellation SPP pipeline and its validation over the Spanish ERGNSS network

Code accompanying the article:

> F. Broncano, P. G. Rodríguez, A. Caro, J. A. Torrecilla-Pinero and A. Cuartero,
> *A Multi-Constellation Standard Point Positioning Pipeline and Its Validation
> over the Spanish Geodetic Network*, IEEE Access (under review).

It contains the end-to-end standard point positioning (SPP) pipeline, which
computes daily station positions from the observation and navigation RINEX files
of the IGN ERGNSS network with broadcast ephemerides only, for GPS, Galileo,
BeiDou and their combination, and the analysis that validates them against the
official IGN coordinates and against the daily PPP series of the Nevada Geodetic
Laboratory (NGL).

Repository: https://github.com/fbroncano/spp-pipeline

## Repository structure

```
├── processor/                 SPP pipeline
│   ├── main.py                entry point: processes station-days and writes to PostGIS
│   ├── manage_files.py        download and reading of RINEX files (IGN ERGNSS server)
│   ├── pre_processor.py       observables, ionosphere-free combination, Hatch smoothing,
│   │                          group delays, troposphere, Sagnac
│   ├── sv_processor.py        satellite positions and clocks from broadcast ephemerides
│   ├── hatchfilter.py         carrier smoothing of the code (Hatch filter)
│   ├── gnss_processor.py      epoch-wise weighted least-squares solution
│   ├── post_processor.py      daily estimators, DOP and quality statistics
│   ├── utils.py               time, file-name and coordinate utilities
│   ├── glonass.py             GLONASS orbit propagation (not used in the article)
│   ├── CONSTANTS.py           configuration (decimation, Hatch window, frequency pairs,
│   │                          weighting, database connection)
│   ├── dao.py, models.py      data-access layer over PostGIS
│   ├── load_stations.py       loads the station catalogue (estaciones_itrf2014.csv)
│   └── load_sitelogs.py       loads antenna and receiver history from EPN site logs
├── analysis/                  validation of the article
│   ├── study.py               stations, period, software version and sampling analysed
│   ├── tables.py              processing statistics, estimators, aggregated solutions,
│   │                          ENU decomposition, inter-annual and per-constellation accuracy
│   ├── run_all.py             writes every table as CSV and LaTeX
│   ├── ngl.py                 downloads the NGL series and compares the daily positions
│   └── rtklib/                comparison with RTKLIB (rnx2rtkp) on a subset of days
├── sql/
│   ├── schema.sql             database schema used in this work
│   └── catalogs.sql           controlled vocabularies (constellations, methods, versions…)
├── patches/
│   └── georinex-nav3.patch    fix for the BeiDou ephemeris defect in georinex 1.16.2
├── postgis.cfg.example        database connection template
└── requirements.txt
```

## Processing

For each station and day the pipeline downloads the 30 s observation file and the
combined navigation file, decimates the observations to 300 s and, epoch by epoch:

1. computes the position and clock of each satellite at the transmission instant
   from its broadcast ephemeris, with the relativistic and Sagnac corrections;
2. forms the ionosphere-free code combination with the frequency pair to which the
   broadcast clock of each system refers (GPS L1/L2, Galileo E1/E5b, BeiDou
   B1I/B3I), smoothed by a Hatch filter (10 epochs) applied to the
   ionosphere-free code and phase, and corrected for the BeiDou group delays
   (TGD1, TGD2);
3. removes the tropospheric delay (Saastamoinen, standard atmosphere) and keeps
   the satellites above a 10° elevation mask;
4. solves position and receiver clock by weighted least squares, with one
   inter-system bias per additional constellation in the combined solution. Each
   observation is weighted by 1/σ², with σ per system (GPS 1.0 m, Galileo 0.5 m,
   BeiDou 1.1 m) taken from the post-fit residuals of the single-system
   solutions. An elevation-dependent model (`WEIGHT_MODEL = 'elevation'`) is
   also available.

The epoch-wise solutions of each day are condensed by five estimators (mean,
median, PDOP-weighted mean, MAD filter with mean, MAD filter with PDOP-weighted
mean) and stored with their quality statistics.

### Versions

The `software` table records the version of every run, and every position and
statistic carries its `run_id`.

| Version | Change |
|---|---|
| 1.1.0 | Version of the first submission. |
| 1.2.0 | The Hatch filter smooths the ionosphere-free combination instead of each frequency separately. Smoothing each frequency and combining afterwards only cancels the code-carrier ionospheric divergence when both filters share the same history; with 300 s decimation the second frequency was reset about twice as often by the cycle-slip test, and the surviving divergence biased the daily positions by up to 1.7 m, mostly to the east and largest in summer. Detected by comparison with RTKLIB and with the NGL series. |
| 1.3.0 | BeiDou group delays (TGD1, TGD2) applied to the B1I/B3I and B1I/B2I combinations; weighted least squares by elevation and system. |
| 1.4.0 | Galileo processed with E1/E5b, the pair to which the I/NAV clocks refer, instead of E1/E5a (E1/E5a would need a BGD correction, which was not applied, and E5a degraded the solution at stations with older antennas); weighting by system only. Results of the article. |

## Requirements

Python 3.12 or later (the results were produced with 3.14) and the packages in
`requirements.txt`:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**`georinex` must be patched.** In version 1.16.2 the RINEX 3 navigation reader
discards an entire ephemeris when an empty field is padded with spaces, which is
how BeiDou writes it. Without the patch no BeiDou ephemeris is read, and the only
trace is an INFO-level message (`malformed line for Cxx`). Apply it to the
installed package:

```bash
SITE="$(python -c 'import georinex, os; print(os.path.dirname(os.path.dirname(georinex.__file__)))')"
patch -p2 --directory="$SITE" < patches/georinex-nav3.patch
```

## Database

Results are stored in PostgreSQL with PostGIS. The data model follows the
information system described in F. Broncano et al., *Electronics* 15(17), 3899
(2026), doi:[10.3390/electronics15173899](https://doi.org/10.3390/electronics15173899),
whose containerised deployment is published as GeoGNSS-PS
(doi:[10.5281/zenodo.20538639](https://doi.org/10.5281/zenodo.20538639)).
`sql/schema.sql` extends that schema with additional per-session statistics
(HDOP, VDOP, 95th percentiles, elevation, azimuth and satellites per system) and
with insert functions that record the frame and epoch of the station
coordinates. **Use `sql/schema.sql` from this repository**, not the schema of
GeoGNSS-PS, since the processor writes the additional columns.

1. Start a PostgreSQL 18 + PostGIS 3.6 server, for example with the GeoGNSS-PS
   image. Give the deployment its own Compose project name (`-p`): without it the
   project takes the name of the directory, and two deployments started from the
   same directory share container and volume.

   ```bash
   cp postgis.cfg.example postgis.cfg      # then edit it; postgis.cfg is ignored by git
   docker compose -p spp-ergnss --env-file postgis.cfg \
       -f <GeoGNSS-PS>/scripts/docker/docker-compose.yml up -d
   ```

2. Load the schema and the catalogues:

   ```bash
   psql -h 127.0.0.1 -p <port> -U <user> -d positions -f sql/schema.sql
   psql -h 127.0.0.1 -p <port> -U <user> -d positions -f sql/catalogs.sql
   ```

The processor and the analysis look up `postgis.cfg` from their own directory
upwards; any `POSTGRES_*` environment variable overrides it.

## Reproducing the article

```bash
# 1. Station metadata
cd processor
python load_stations.py --epoch 2025.5           # station catalogue, ITRF2014
python load_sitelogs.py --dir <site_logs>        # EPN site logs (https://epncb.oma.be/ftp/station/log/)

# 2. SPP processing: 13 stations, 2024-2025 by default (about 3 h with 12 processes)
python main.py --workers 12
cd ..

# 3. Tables of the article (CSV and LaTeX in results/all/)
python -m analysis.run_all
python -m analysis.run_all --sampling d1_15      # only the 1st and 15th of each month

# 4. Comparison with the NGL PPP series
python -m analysis.ngl --download

# 5. Comparison with RTKLIB (first day of every month)
python -m analysis.rtklib download
python -m analysis.rtklib run --rnx2rtkp /path/to/rnx2rtkp
python -m analysis.rtklib compare
python -m analysis.rtklib timing --rnx2rtkp /path/to/rnx2rtkp
```

RTKLIB is not included. The results were obtained with `rnx2rtkp` from the
demo5 b34L release (https://github.com/rtklibexplorer/RTKLIB), built from
`app/consapp/rnx2rtkp/gcc`; on macOS the makefile needs
`CFLAGS='-std=gnu99 -O3 -D_DARWIN_C_SOURCE ...'` and `LDLIBS=-lm`. The
settings are in `analysis/rtklib/spp.conf`.

A session enters the analysis only if its solution has redundancy (at least
five satellites on average over the day, `study.MIN_SATELLITES`). The
processing statistics are medians over the sessions, since a few divergent
epochs make daily means and maxima meaningless.

A station-day whose files are missing on the server is logged as
`[WORKER ERROR]` and does not stop the run.

| Article element | Script |
|---|---|
| Processing statistics per constellation and multi-constellation | `tables.processing_statistics` |
| Estimator comparison (daily error, repeatability) and paired Wilcoxon tests | `tables.estimator_comparison` |
| Aggregated solutions and ENU decomposition | `tables.aggregated_solutions` |
| Inter-annual consistency | `tables.interannual` |
| Accuracy per constellation | `tables.constellation_accuracy` |
| Comparison with NGL | `analysis/ngl.py` |
| Comparison with RTKLIB and processing time | `python -m analysis.rtklib` |

The station coordinates in `estaciones_itrf2014.csv` are the official IGN ETRS89
(ETRF2000) coordinates transformed to ITRF2014 at epoch 2025.5. LPAL, whose
official coordinates are given in REGCAN95, takes the median of its NGL series
over 2024–2025.

## Known limitations

- For each band the pipeline takes the first observation code in the RINEX header
  that matches the band, regardless of the tracking attribute. At stations that
  declare `C2S` before `C2W`, the second GPS frequency is resolved to the civil
  code rather than to the P(Y) code to which the broadcast clock refers.
- BeiDou satellites solved with the B1C/B2a pair are not corrected for group
  delays (their delays are broadcast in a navigation message absent from these
  files). No Galileo BGD is applied: none is needed for E1/E5b, but the E1/E5a
  pair, used only when E5b is missing, is left uncorrected.
- No antenna phase-centre model (ANTEX) is applied: the solution refers to the
  antenna reference point.
- The georinex reader reports a few malformed Galileo records (under a hundred in
  about 28 000 station-days); those ephemerides are skipped.

## Data sources

- GNSS observation and navigation files: Instituto Geográfico Nacional, ERGNSS
  network (`https://datos-geodesia.ign.es/ERGNSS/`).
- Station site logs: EPN Central Bureau (`https://epncb.oma.be/`).
- Reference series: Nevada Geodetic Laboratory, IGS20 `tenv3` series
  (`https://geodesy.unr.edu/`), downloaded by `analysis/ngl.py`.

None of them is redistributed with the code.

## Licence and citation

MIT licence (see `LICENSE`). If you use this code, please cite the article and the
archived release (see `CITATION.cff`).
