# core/universe.py
import pandas as pd
import logging
import requests
import io
from typing import List

logger = logging.getLogger(__name__)

def get_sp500_ndx_universe(fallback_list: List[str]) -> List[str]:
    """Dynamically fetches and merges S&P 500 and Nasdaq 100 tickers with browser spoofing."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    }
    
    try:
        # Fetch S&P 500
        logger.info("Fetching S&P 500 components from Wikipedia...")
        sp500_url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        response_sp500 = requests.get(sp500_url, headers=headers, timeout=15)
        response_sp500.raise_for_status()
        sp500_df = pd.read_html(io.StringIO(response_sp500.text))[0]
        sp500_tickers = sp500_df['Symbol'].tolist()

        # Fetch Nasdaq 100
        logger.info("Fetching Nasdaq 100 components from Wikipedia...")
        ndx_url = 'https://en.wikipedia.org/wiki/Nasdaq-100'
        response_ndx = requests.get(ndx_url, headers=headers, timeout=15)
        response_ndx.raise_for_status()
        tables = pd.read_html(io.StringIO(response_ndx.text))
        
        ndx_tickers = []
        for df in tables:
            if 'Ticker' in df.columns:
                ndx_tickers = df['Ticker'].tolist()
                break

        # Merge, deduplicate, and format for yfinance (e.g., BRK.B -> BRK-B)
        universe = list(set(sp500_tickers + ndx_tickers))
        clean_universe = [ticker.replace('.', '-') for ticker in universe]
        
        logger.info(f"Universe compiled: {len(clean_universe)} unique equities.")
        return clean_universe
    
    except Exception as e:
        logger.error(f"Failed to compile dynamic universe: {e}. Using fallback list.")
        return fallback_list