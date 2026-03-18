import pandas as pd
from typing import Dict, Any
import config
from core.detector_base import PatternDetector

class WyckoffSpringDetector(PatternDetector):
    @property
    def name(self) -> str:
        return "Wyckoff Spring"

    def __init__(self, 
                 lookback_period: int = config.WYCKOFF_STRUCTURAL_LOOKBACK, 
                 volume_surge_threshold: float = config.WYCKOFF_VOL_SURGE_MULT, 
                 scan_window: int = config.WYCKOFF_SCAN_WINDOW):
        self.lookback_period = lookback_period
        self.volume_surge_threshold = volume_surge_threshold
        self.scan_window = scan_window 
    
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df['High'] - df['Low']
        high_close = (df['High'] - df['Close'].shift()).abs()
        low_close = (df['Low'] - df['Close'].shift()).abs()
        return pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(period).mean()
    
    def scan(self, df: pd.DataFrame) -> Dict[str, Any]:
        if len(df) < (self.lookback_period + self.scan_window + 5):
            return {"signal_found": False}

        avg_vol_series = df['Volume'].rolling(20).mean()

        for i in range(1, self.scan_window + 1):
            end_idx = -i if i > 1 else None
            window_df = df.iloc[:end_idx] if end_idx else df
            
            avg_volume = avg_vol_series.loc[window_df.index[-1]]
            
            historical_window = window_df.iloc[-(self.lookback_period + 3):-3]
            range_low = historical_window['Low'].min()

            c1, c2, c3 = window_df.iloc[-3], window_df.iloc[-2], window_df.iloc[-1]

            sweep_occurred = c2['Low'] < range_low or c1['Low'] < range_low
            recovery_occurred = c3['Close'] > range_low
            
            if sweep_occurred and recovery_occurred:
                recovery_volume = c3['Volume']
                
                if recovery_volume > (avg_volume * self.volume_surge_threshold):
                    quality = round(recovery_volume / avg_volume, 2)
                    invalidation = min(c1['Low'], c2['Low'], c3['Low']) 
                    atr = self.calculate_atr(window_df).iloc[-1]
                    atr_risk = round(abs(c3['Close'] - invalidation) / atr, 2)
                    return {
                        "signal_found": True,
                        "setup_type": "Bullish Reversal",
                        "quality_score": quality, 
                        "trigger_price": c3['Close'], 
                        "atr_risk_score": atr_risk,
                        "invalidation_level": invalidation, 
                        "metadata": {
                            "volume_multiplier": quality,
                            "distance_from_range_low": round(range_low - invalidation, 4),
                            "periods_ago": i - 1
                        }
                    }

        return {"signal_found": False}