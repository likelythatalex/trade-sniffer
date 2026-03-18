import logging
from pathlib import Path
from typing import List

# --- PATH CONFIGURATION ---
BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
LOG_DIR: Path = BASE_DIR / "logs"
JOURNAL_DIR: Path = BASE_DIR / "journal"
PLOTS_DIR: Path = BASE_DIR / "plots"
DETECTORS_DIR: Path = BASE_DIR / "detectors"

# Ensure runtime directories exist
for directory in [DATA_DIR, LOG_DIR, JOURNAL_DIR, PLOTS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# --- RISK & PORTFOLIO PARAMETERS ---
ACCOUNT_CAPITAL: float = 5000.00
MAX_RISK_PER_TRADE_PCT: float = 0.01  # 1% risk ($50 maximum exposure per trade)
MAX_DRAWDOWN_LIMIT_PCT: float = 0.20  # Hard stop limit for the system

# --- DATA INGESTION ---
# Extracted from data_handler.py
DEFAULT_HISTORY_PERIOD: str = "2y"

# --- SCREENER UNIVERSE ---
FALLBACK_TICKERS: List[str] = [
    "AAPL", "MSFT", "NVDA", "TSLA", "META", "AMZN", "AMD", "NFLX", "COIN", "PLTR",
    "GOOGL", "AVGO", "COST", "PEP", "ADBE", "CSCO", "INTC", "QCOM", "TXN", "AMAT"
]

# --- MULTI-TIMEFRAME ANALYSIS (MTFA) ---
# Extracted to properly handle Top-Down screening
MACRO_TIMEFRAME: str = "1wk" # Used to identify structural zones (The Trap)
MICRO_TIMEFRAME: str = "1d"  # Used to identify execution triggers (The Entry)
# For standard loops, you can still compile them into a list:
TIMEFRAMES: List[str] = [MACRO_TIMEFRAME, MICRO_TIMEFRAME]

# --- QUANTITATIVE ALGORITHM THRESHOLDS ---
# These "Magic Numbers" were previously hardcoded in detectors/*.py
# Moving them here allows for centralized tuning and future backtest optimizations.

# 1. Fair Value Gap (FVG) Parameters
FVG_MIN_GAP_ATR_MULT: float = 0.5   # Gap must be at least 50% of the ATR
FVG_VOL_SURGE_MULT: float = 1.2     # Displacement volume must be 1.2x average
FVG_LOOKBACK_WINDOW: int = 1        # Only flag if completed on the most recent closed candle

# 2. Wyckoff Spring Parameters
WYCKOFF_STRUCTURAL_LOOKBACK: int = 20 # Periods to define the Phase A/B trading range
WYCKOFF_VOL_SURGE_MULT: float = 1.3   # Capitulation recovery volume must be 1.3x average
WYCKOFF_SCAN_WINDOW: int = 1          # Only flag if the spring completed on the most recent candle

# --- 3. Momentum Trigger Parameters (Daily Execution) ---
MOMENTUM_RSI_PERIOD: int = 14
MOMENTUM_RSI_OVERSOLD: int = 30
MOMENTUM_RSI_OVERBOUGHT: int = 70
MOMENTUM_EMA_FAST: int = 9
MOMENTUM_EMA_SLOW: int = 21
MOMENTUM_RSI_LOOKBACK: int = 5 # Days to look back for the RSI sweep

# --- LOGGING CONFIGURATION ---
LOG_FILE: Path = LOG_DIR / "screener.log"
LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_LEVEL: int = logging.INFO