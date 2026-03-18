import pandas as pd
from typing import Dict, Any
import config
from core.detector_base import PatternDetector

class FairValueGapDetector(PatternDetector):
    @property
    def name(self) -> str:
        return "Fair Value Gap (FVG)"

    def __init__(self, 
                 min_gap_atr_multiplier: float = config.FVG_MIN_GAP_ATR_MULT, 
                 volume_surge_threshold: float = config.FVG_VOL_SURGE_MULT, 
                 lookback_days: int = config.FVG_LOOKBACK_WINDOW):
        # Now strictly configuration-driven
        self.min_gap_atr_multiplier = min_gap_atr_multiplier
        self.volume_surge_threshold = volume_surge_threshold
        self.lookback_days = lookback_days 

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df['High'] - df['Low']
        high_close = (df['High'] - df['Close'].shift()).abs()
        low_close = (df['Low'] - df['Close'].shift()).abs()
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        return true_range.rolling(period).mean()

    def scan(self, df: pd.DataFrame) -> Dict[str, Any]:
        if len(df) < 20 + self.lookback_days:
            return {"signal_found": False}

        atr_series = self.calculate_atr(df)
        avg_vol_series = df['Volume'].rolling(20).mean()

        for i in range(1, self.lookback_days + 1):
            end_idx = -i if i > 1 else None
            window_df = df.iloc[:end_idx] if end_idx else df
                
            atr = atr_series.loc[window_df.index[-1]]
            avg_volume = avg_vol_series.loc[window_df.index[-1]]

            c1, c2, c3 = window_df.iloc[-3], window_df.iloc[-2], window_df.iloc[-1]

            # --- BULLISH FVG LOGIC ---
            bullish_gap_size = c3['Low'] - c1['High']
            if bullish_gap_size > (atr * self.min_gap_atr_multiplier):
                if c2['Volume'] > (avg_volume * self.volume_surge_threshold):
                    
                    # NEW: Calculate ATR Risk Score before returning
                    risk_dist = abs(c1['High'] - c2['Low'])
                    atr_risk = round(risk_dist / atr, 2)
                    
                    return {
                        "signal_found": True,
                        "setup_type": "Bullish",
                        "quality_score": round((c2['Volume'] / avg_volume), 2),
                        "atr_risk_score": atr_risk,  # <-- Added here
                        "trigger_price": c1['High'],
                        "invalidation_level": c2['Low'],
                        "metadata": {"gap_size_atr_ratio": round(bullish_gap_size / atr, 2), "periods_ago": i - 1}
                    }

            # --- BEARISH FVG LOGIC ---
            bearish_gap_size = c1['Low'] - c3['High']
            if bearish_gap_size > (atr * self.min_gap_atr_multiplier):
                if c2['Volume'] > (avg_volume * self.volume_surge_threshold):
                    
                    # NEW: Calculate ATR Risk Score before returning
                    risk_dist = abs(c1['Low'] - c2['High'])
                    atr_risk = round(risk_dist / atr, 2)
                    
                    return {
                        "signal_found": True,
                        "setup_type": "Bearish",
                        "quality_score": round((c2['Volume'] / avg_volume), 2),
                        "atr_risk_score": atr_risk,  # <-- Added here
                        "trigger_price": c1['Low'],
                        "invalidation_level": c2['High'],
                        "metadata": {"gap_size_atr_ratio": round(bearish_gap_size / atr, 2), "periods_ago": i - 1}
                    }

        return {"signal_found": False}