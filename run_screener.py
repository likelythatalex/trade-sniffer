import logging
import sys
from typing import Dict, Any

import config
from core.data_handler import DataHandler
from core.visualizer import Visualizer
from core.journal import JournalManager
from core.universe import get_sp500_ndx_universe

# Explicitly import detectors for routing
from detectors.fvg import FairValueGapDetector
from detectors.wyckoff import WyckoffSpringDetector
from detectors.momentum import MomentumTriggerDetector

logging.basicConfig(
    level=config.LOG_LEVEL, 
    format=config.LOG_FORMAT, 
    handlers=[logging.FileHandler(config.LOG_FILE), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def calculate_position_size(entry_price: float, stop_loss: float) -> float:
    risk_amount = config.ACCOUNT_CAPITAL * config.MAX_RISK_PER_TRADE_PCT
    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share <= 0: return 0.0
    return round(risk_amount / risk_per_share, 4)

def process_ticker(ticker: str, timeframe: str, data_handler, visualizer, journal, detectors, macro_zone: Dict[str, Any] = None) -> Dict[str, Any]:
    """Processes a ticker and returns the signal data if a valid setup is found."""
    try:
        df = data_handler.fetch_ohlcv(ticker, timeframe)
        if df.empty: return None
        
        for detector in detectors:
            signal_data: Dict[str, Any] = detector.scan(df)
            
            if signal_data and signal_data.get("signal_found"):
                
                # --- 🎯 ZONE CONFLUENCE ENFORCEMENT (PHASE 2 ONLY) ---
                if macro_zone:
                    current_price = df['Close'].iloc[-1]
                    is_bullish_micro = "Bullish" in signal_data.get("setup_type", "")
                    is_bullish_macro = "Bullish" in macro_zone.get("type", "")
                    
                    # 1. Directional Sync: Micro direction MUST match Macro direction
                    if is_bullish_micro != is_bullish_macro:
                        logger.debug(f"[{ticker}] Momentum triggered, but failed Directional Sync. Skipping.")
                        continue
                        
                    # 2. Geographical Sync: Daily price MUST be inside the Weekly structural box
                    if not (macro_zone['low'] <= current_price <= macro_zone['high']):
                        logger.debug(f"[{ticker}] Momentum triggered, but price (${current_price:.2f}) is outside the Macro Zone. Skipping.")
                        continue
                        
                    logger.info(f"🎯 CONFLUENCE TRIGGERED: {ticker} is executing inside the Macro Zone!")

                # --- LOGGING & PLOTTING ---
                logger.info(f"🔥 Setup Identified: {detector.__class__.__name__} on {ticker} ({timeframe})")
                
                entry = signal_data.get("trigger_price", df['Close'].iloc[-1])
                sl = signal_data.get("invalidation_level", 0)
                signal_data["position_size"] = calculate_position_size(entry, sl) if sl else 0
                signal_data["risk_amount"] = config.ACCOUNT_CAPITAL * config.MAX_RISK_PER_TRADE_PCT
                
                plot_path = visualizer.generate_chart(df, ticker, signal_data, f"{detector.__class__.__name__}_{timeframe}")
                journal.log_pending_setup(ticker, timeframe, detector.__class__.__name__, signal_data, plot_path)
                
                return signal_data # Return the valid setup
                
    except Exception as e:
        logger.error(f"Error processing {ticker} on {timeframe}: {e}")
        
    return None

def main():
    logger.info("Initializing Top-Down MTFA Screener Pipeline...")
    
    data_handler = DataHandler(cache_dir=config.DATA_DIR)
    visualizer = Visualizer(output_dir=config.PLOTS_DIR)
    journal = JournalManager(output_path=config.JOURNAL_DIR / "trading_journal.csv")
    
    macro_detectors = [FairValueGapDetector(), WyckoffSpringDetector()]
    micro_detectors = [MomentumTriggerDetector()]

    target_tickers = get_sp500_ndx_universe(fallback_list=config.FALLBACK_TICKERS)
    
    # Dictionary to hold the structural parameters of the macro setups
    macro_watchlist: Dict[str, Dict[str, Any]] = {}

    # --- PHASE 1: MACRO SCREENING (The Radar) ---
    logger.info(f"--- STARTING PHASE 1: MACRO SCAN ({config.MACRO_TIMEFRAME}) ---")
    for ticker in target_tickers:
        macro_signal = process_ticker(ticker, config.MACRO_TIMEFRAME, data_handler, visualizer, journal, macro_detectors)
        
        # If a weekly setup is found, map its exact boundaries
        if macro_signal:
            trigger = macro_signal.get("trigger_price", 0)
            inval = macro_signal.get("invalidation_level", 0)
            macro_watchlist[ticker] = {
                "high": max(trigger, inval),
                "low": min(trigger, inval),
                "type": macro_signal.get("setup_type", "")
            }

    # --- PHASE 2: MICRO SCREENING (The Sniper) ---
    logger.info(f"--- STARTING PHASE 2: MICRO SCAN ({config.MICRO_TIMEFRAME}) ---")
    
    if not macro_watchlist:
        logger.info("No Macro structural zones identified. Skipping Micro scan.")
    else:
        logger.info(f"Executing Micro scan on {len(macro_watchlist)} watchlisted tickers: {list(macro_watchlist.keys())}")
        for ticker, zone_data in macro_watchlist.items():
            # Pass the macro_zone dictionary to enforce confluence
            process_ticker(ticker, config.MICRO_TIMEFRAME, data_handler, visualizer, journal, micro_detectors, macro_zone=zone_data)

    logger.info("Screener execution completed successfully.")

if __name__ == "__main__":
    main()