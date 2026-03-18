import os
import sys
import pandas as pd
from pathlib import Path

# Add the parent directory to Python's path so we can import config
sys.path.append(str(Path(__file__).resolve().parent.parent))
import config

data_dir = config.DATA_DIR

print(f"Loading local Parquet cache into memory from {data_dir}...\n")

for filename in os.listdir(data_dir):
    if filename.endswith(".parquet"):
        ticker = filename.split("_")[0]
        file_path = os.path.join(data_dir, filename)
        
        temp_df = pd.read_parquet(file_path)
        globals()[f"df_{ticker}"] = temp_df
        print(f"Loaded {len(temp_df)} rows for: df_{ticker}")

print("\nSuccess. Check your Variable Explorer (top right) to inspect the dataframes.")