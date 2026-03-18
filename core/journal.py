import logging
import csv
from pathlib import Path
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class JournalManager:
    def __init__(self, output_path: Path):
        self.output_path = output_path
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not self.output_path.exists():
            # Added ATR_Risk_Score
            headers = [
                "Timestamp", "Ticker", "Timeframe", "Setup_Type", 
                "Quality_Score", "ATR_Risk_Score", "Trigger_Price", "Invalidation_Level", 
                "Risk_Amount", "Est_Position_Size", "Chart_Path", 
                "Trade_Taken", "Outcome_R" 
            ]
            try:
                with open(self.output_path, mode='w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
            except Exception as e:
                logger.critical(f"Failed to initialize journal file: {e}")

    def log_pending_setup(self, ticker: str, timeframe: str, detector_name: str, signal_data: Dict[str, Any], plot_path: str):
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ticker,
            timeframe,
            f"{detector_name} - {signal_data.get('setup_type', 'Unknown')}",
            signal_data.get("quality_score", 0.0),
            signal_data.get("atr_risk_score", 0.0), # Logged here
            signal_data.get("trigger_price", 0.0),
            signal_data.get("invalidation_level", 0.0),
            signal_data.get("risk_amount", 0.0),
            signal_data.get("position_size", 0.0),
            str(plot_path),
            "", 
            ""  
        ]
        
        try:
            with open(self.output_path, mode='a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(row)
        except Exception as e:
            logger.error(f"Failed to write to journal for {ticker}: {e}")