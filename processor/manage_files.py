import os
import json
import requests
from utils import datestr_filename, nav_filename, obs_filename, print_log
from concurrent.futures import ThreadPoolExecutor
from CONSTANTS import MAX_WORKERS, TEST, SAVE_FOLDER, DOWNLOAD_FOLDER, INTERVAL
import pandas as pd
import georinex as gr

import numpy as np

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)
    
def download_year(station: str, year: int) -> None:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for month in range(1, 13):
            executor.submit(download_month, station, month, year)



def download_month(station: str, month: int, year: int) -> None:
    if month in [1, 3, 5, 7, 8, 10, 12]:
        days = 31
    elif month in [4, 6, 9, 11]:
        days = 30
    else:
        days = 29 if year % 4 == 0 else 28

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for day in range(1, days-1):
            executor.submit(download_station_day, station, day, month, year)
        


def download_station_day(station: str, day: int, month: int, year: int, navigation: bool = False) -> None:
    date_str = datestr_filename(day, month, year)
    
    # Build the URLs
    base_url = f"https://datos-geodesia.ign.es/ERGNSS/diario_30s/{year}/{date_str}"
    obs_name = obs_filename(station, day, month, year)
    nav_name = nav_filename(station, day, month, year)
    
    url_obs = f"{base_url}/{obs_name}"
    url_nav = f"{base_url}/{nav_name}"
    
    # Create the download folder if it does not exist
    os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

    if navigation:
        # Download navigation file
        print(f"Downloading navigation file: {nav_name}")
        try:
            response = requests.get(url_nav, timeout=30)
            response.raise_for_status()
            nav_path = os.path.join(DOWNLOAD_FOLDER, nav_name)
            with open(nav_path, "wb") as f:
                f.write(response.content)
            print(f"  ✓ Navigation file downloaded: {nav_path}")
        except requests.exceptions.RequestException as e:
            print(f"  ✗ Error downloading navigation file: {e}")
    else:
        # Download observation file
        print_log(f"Downloading observation file: {obs_name}", TEST)

        try:
            response = requests.get(url_obs, timeout=30)
            response.raise_for_status()
            obs_path = os.path.join(DOWNLOAD_FOLDER, obs_name)
            with open(obs_path, "wb") as f:
                f.write(response.content)
            print_log(f"  ✓ Observation file downloaded: {obs_path}", TEST)
        except requests.exceptions.RequestException as e:
            print(f"  ✗ Error downloading observation file: {e}")
    



def clean_file(rn_df: pd.DataFrame) -> pd.DataFrame:
    return rn_df.dropna(axis=0, how="all")


def load_files(file: str, navigation: bool = False) -> tuple[pd.DataFrame, dict]:
    print(f"Loading file {file}")

    kwargs = {"use": {"G", "E", "C"}}
    if not navigation:
        # Only observations are decimated: ephemerides come every ~2 h and
        # filtering them by time would leave satellites without an orbit.
        kwargs["interval"] = INTERVAL

    rinex_xr = gr.load(file, **kwargs)
    rinex_df = rinex_xr.to_dataframe()
    print(f"File {file} loaded")

    rinex_attrs = dict(rinex_xr.attrs)

    return rinex_df, rinex_attrs


def clean_nav_df(nav_df: pd.DataFrame) -> pd.DataFrame:
    
    # Drop empty rows
    nav_df = clean_file(nav_df)

    # Strip the "_" suffix from SV names and drop duplicated records
    nav_df = nav_df.reset_index()
    nav_df['sv'] = nav_df['sv'].str.split('_').str[0]
    nav_df = nav_df.drop_duplicates(subset=['time', 'sv'], keep='first')
    nav_df = nav_df.set_index(['time', 'sv'])   
    return nav_df


def save_file(rn_df: pd.DataFrame, attrs: dict, station: str, day: int, month: int, year: int, navigation: bool = False) -> None:
    suffix = "MN" if navigation else "MO"
    date = f"{day:02d}{month:02d}{year:04d}"
    
    rn_df.to_csv(f"{SAVE_FOLDER}/{station}_{date}_{suffix}.csv")
    with open(f"{SAVE_FOLDER}/{station}_{date}_{suffix}.json", "w") as f:
        json.dump(attrs, f, indent=2, cls=NumpyEncoder)



def open_file(station: str, day: int, month: int, year: int, navigation: bool = False, save: bool = True) -> tuple[pd.DataFrame, dict]:

    # File naming
    suffix = "MN" if navigation else "MO"
    date = f"{day:02d}{month:02d}{year:04d}"
    csv_path = f"{SAVE_FOLDER}/{station}_{date}_{suffix}.csv"
    json_path = f"{SAVE_FOLDER}/{station}_{date}_{suffix}.json"

    # If a cached copy exists, load and return it
    if os.path.isfile(csv_path) and os.path.isfile(json_path):
        # Load attributes
        with open(json_path, 'r') as f:
            attrs = json.load(f)

        return pd.read_csv(csv_path, index_col=['time', 'sv']), attrs

    else:
        # Standard IGS file name
        rn_file = nav_filename(station, day, month, year) if navigation else obs_filename(station, day, month, year)
        rn_file = f"{DOWNLOAD_FOLDER}/{rn_file}"
        
        # Download it if not present
        if not os.path.exists(rn_file):
            download_station_day(station, day, month, year, navigation)
        
        # Read the RINEX file into a dataframe
        rn_df, attrs = load_files(rn_file, navigation)
        rn_df = clean_nav_df(rn_df) if navigation else clean_file(rn_df)
        if save:
            save_file(rn_df, attrs, station, day, month, year, navigation)

        return rn_df, attrs


def remove_file(station: str, day: int, month: int, year: int, navigation: bool = False) -> None:
    file = nav_filename(station, day, month, year) if navigation else obs_filename(station, day, month, year)
    path = f"{DOWNLOAD_FOLDER}/{file}"
    if os.path.exists(path):  # may not exist if it came from the cache
        os.remove(path)

if __name__ == "__main__":
    # open_file("CACE", 1, 3, 2025)
    remove_file("CACE", 5, 12, 2025, True)
    remove_file("CACE", 5, 12, 2025, False)