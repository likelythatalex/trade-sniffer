import logging
import yfinance as yf
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import pytz
import config

logger = logging.getLogger(__name__)

class DataHandler:
    """Handles OHLCV data fetching with local Parquet delta-updating."""
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def fetch_ohlcv(self, ticker: str, timeframe: str = "1d", default_lookback: str = config.DEFAULT_HISTORY_PERIOD) -> pd.DataFrame:
        cache_file = self.cache_dir / f"{ticker}_{timeframe}_cache.parquet"
        
        # Scenario 1: No cache exists. Do a full historical download.
        if not cache_file.exists():
            logger.debug(f"No cache for {ticker}. Fetching full {default_lookback} history.")
            df = self._download_yfinance(ticker, timeframe, period=default_lookback)
            if not df.empty:
                df.to_parquet(cache_file)
            return df

        # Scenario 2: Cache exists. Load it and find the last date.
        try:
            cached_df = pd.read_parquet(cache_file)
            if cached_df.empty:
                raise ValueError("Cached DataFrame is empty.")
                
            # Ensure the index is timezone-naive for comparison
            if cached_df.index.tz is not None:
                cached_df.index = cached_df.index.tz_localize(None)

            last_date = cached_df.index.max()
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

            # If the data is already up to date (last date is today or yesterday depending on market close)
            # We add a 1-day buffer because yfinance end dates are exclusive
            if last_date >= (today - timedelta(days=1)):
                return cached_df

            # Fetch only the missing data (Delta Fetch)
            start_fetch_date = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
            logger.debug(f"Updating {ticker} ({timeframe}) from {start_fetch_date} to today.")
            
            delta_df = self._download_yfinance(ticker, timeframe, start=start_fetch_date)
            
            if not delta_df.empty:
                # Merge the old and new data, dropping any accidental overlaps
                combined_df = pd.concat([cached_df, delta_df])
                combined_df = combined_df[~combined_df.index.duplicated(keep='last')]
                combined_df.sort_index(inplace=True)
                
                # Save the updated cache back to disk
                combined_df.to_parquet(cache_file)
                return combined_df
            else:
                return cached_df # Return existing if no new data was found

        except Exception as e:
            logger.warning(f"Cache corrupted or failed for {ticker}: {e}. Forcing full fetch.")
            df = self._download_yfinance(ticker, timeframe, period=default_lookback)
            if not df.empty:
                df.to_parquet(cache_file)
            return df

    def _download_yfinance(self, ticker: str, timeframe: str, period: str = None, start: str = None) -> pd.DataFrame:
        """Helper method to isolate the yfinance network call and standardize column structures."""
        try:
            if start:
                df = yf.download(ticker, interval=timeframe, start=start, progress=False)
            else:
                df = yf.download(ticker, interval=timeframe, period=period, progress=False)
                
            if df.empty:
                return pd.DataFrame()

            # Flatten multi-index if yfinance returns it
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df.rename(columns={"Date": "Datetime"}, inplace=True)
            df.index.name = "Datetime"
            df.dropna(inplace=True)
            
            # Strip timezone info from index to prevent parquet serialization conflicts
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

            return df
        except Exception as e:
            logger.error(f"Network fetch failed for {ticker}: {e}")
            return pd.DataFrame()