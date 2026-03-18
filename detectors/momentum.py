import pandas as pd
from typing import Dict, Any
import config
from core.detector_base import PatternDetector

class MomentumTriggerDetector(PatternDetector):
    @property
    def name(self) -> str:
        return "RSI Sweep + Fast EMA"

    def __init__(self, 
                 rsi_period: int = config.MOMENTUM_RSI_PERIOD,
                 rsi_os: int = config.MOMENTUM_RSI_OVERSOLD,
                 rsi_ob: int = config.MOMENTUM_RSI_OVERBOUGHT,
                 ema_fast: int = config.MOMENTUM_EMA_FAST,
                 ema_slow: int = config.MOMENTUM_EMA_SLOW,
                 rsi_lookback: int = config.MOMENTUM_RSI_LOOKBACK):
        
        self.rsi_period = rsi_period
        self.rsi_os = rsi_os
        self.rsi_ob = rsi_ob
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_lookback = rsi_lookback

    def calculate_rsi(self, series: pd.Series) -> pd.Series:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/self.rsi_period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/self.rsi_period, adjust=False).mean()
        return 100 - (100 / (1 + (gain / loss)))

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df['High'] - df['Low']
        high_close = (df['High'] - df['Close'].shift()).abs()
        low_close = (df['Low'] - df['Close'].shift()).abs()
        return pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(period).mean()

    def scan(self, df: pd.DataFrame) -> Dict[str, Any]:
        if len(df) < self.ema_slow + self.rsi_period:
            return {"signal_found": False}

        rsi = self.calculate_rsi(df['Close'])
        ema_f = df['Close'].ewm(span=self.ema_fast, adjust=False).mean()
        ema_s = df['Close'].ewm(span=self.ema_slow, adjust=False).mean()
        atr = self.calculate_atr(df).iloc[-1]

        c0_ema_f, c0_ema_s = ema_f.iloc[-1], ema_s.iloc[-1] 
        c1_ema_f, c1_ema_s = ema_f.iloc[-2], ema_s.iloc[-2] 

        # --- BULLISH TRIGGER LOGIC ---
        if (c0_ema_f > c0_ema_s) and (c1_ema_f <= c1_ema_s):
            recent_rsi = rsi.iloc[-(self.rsi_lookback + 1):-1]
            if (recent_rsi < self.rsi_os).any() and (rsi.iloc[-1] > self.rsi_os):
                sl = df['Low'].iloc[-self.rsi_lookback:].min()
                trigger = df['Close'].iloc[-1]
                
                return {
                    "signal_found": True,
                    "setup_type": "Bullish Trigger",
                    "quality_score": round(rsi.iloc[-1], 2), 
                    "atr_risk_score": round(abs(trigger - sl) / atr, 2),
                    "trigger_price": trigger,
                    "invalidation_level": sl,
                    "metadata": {"periods_ago": 0}
                }

        # --- BEARISH TRIGGER LOGIC ---
        if (c0_ema_f < c0_ema_s) and (c1_ema_f >= c1_ema_s):
            recent_rsi = rsi.iloc[-(self.rsi_lookback + 1):-1]
            if (recent_rsi > self.rsi_ob).any() and (rsi.iloc[-1] < self.rsi_ob):
                sl = df['High'].iloc[-self.rsi_lookback:].max()
                trigger = df['Close'].iloc[-1]
                
                return {
                    "signal_found": True,
                    "setup_type": "Bearish Trigger",
                    "quality_score": round(rsi.iloc[-1], 2),
                    "atr_risk_score": round(abs(trigger - sl) / atr, 2),
                    "trigger_price": trigger,
                    "invalidation_level": sl,
                    "metadata": {"periods_ago": 0}
                }

        return {"signal_found": False}