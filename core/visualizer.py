import logging
import mplfinance as mpf
import pandas as pd
from pathlib import Path
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class Visualizer:
    """Generates localized OHLCV charts highlighting detected setups."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def generate_chart(self, df: pd.DataFrame, ticker: str, signal_data: Dict[str, Any], detector_name: str) -> str:
        # Slice for recent context (last 60 candles)
        plot_df = df.tail(60).copy()
        
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ticker}_{detector_name}_{timestamp_str}.png"
        filepath = self.output_dir / filename

        # --- EXTRACT SIGNAL METADATA ---
        setup_type = signal_data.get("setup_type", "Unknown")
        trigger = signal_data.get("trigger_price", 0.0)
        inval = signal_data.get("invalidation_level", 0.0)
        q_score = signal_data.get("quality_score", 0.0)
        atr_risk = signal_data.get("atr_risk_score", 0.0)
        periods_ago = signal_data.get("metadata", {}).get("periods_ago", 0)

        is_bullish = "Bullish" in setup_type
        theme_color = 'green' if is_bullish else 'red'

        title_str = (
            f"{ticker} | [{setup_type.upper()}] {detector_name}\n"
            f"Entry: ${trigger:.2f} | Stop: ${inval:.2f} | Q-Score: {q_score} | ATR Risk: {atr_risk}"
        )

        hlines = dict(
            hlines=[trigger, inval],
            colors=['g', 'r'],
            linestyle='--',
            linewidths=1.5
        )
        
        fill_between = dict(y1=trigger, y2=inval, color=theme_color, alpha=0.1)

        addplots = []
        
        # --- DYNAMIC INDICATOR RENDERING (MOMENTUM ONLY) ---
        # Only clutter the chart with EMAs and RSI if this is a Phase 2 Execution trigger
        is_momentum = "RSI" in detector_name or "Momentum" in detector_name
        
        if is_momentum:
            # 1. Calculate on the FULL dataframe to ensure EMA/RSI accuracy
            ema_fast = df['Close'].ewm(span=9, adjust=False).mean()
            ema_slow = df['Close'].ewm(span=21, adjust=False).mean()
            
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            rsi = 100 - (100 / (1 + (gain / loss)))

            # 2. Slice to match the 60-period visual window
            ema_f_plot = ema_fast.tail(60)
            ema_s_plot = ema_slow.tail(60)
            rsi_plot = rsi.tail(60)

            # 3. Append EMAs to the main price panel (panel=0)
            addplots.append(mpf.make_addplot(ema_f_plot, color='aqua', width=1.5, panel=0))
            addplots.append(mpf.make_addplot(ema_s_plot, color='fuchsia', width=1.5, panel=0))
            
            # 4. Append RSI to a new lower panel (panel=2, since Volume is panel=1)
            addplots.append(mpf.make_addplot(rsi_plot, panel=2, color='white', ylabel='RSI (14)'))
            addplots.append(mpf.make_addplot([70]*60, panel=2, color='red', linestyle='--', width=1))
            addplots.append(mpf.make_addplot([30]*60, panel=2, color='green', linestyle='--', width=1))

        # --- CANDLE HIGHLIGHTING ---
        target_idx = len(plot_df) - 1 - periods_ago
        if 2 <= target_idx < len(plot_df):
            marker_series = pd.Series(index=plot_df.index, dtype=float)
            for offset in [0, 1, 2]:
                idx = target_idx - offset
                if is_bullish:
                    marker_series.iloc[idx] = plot_df['Low'].iloc[idx] * 0.98
                else:
                    marker_series.iloc[idx] = plot_df['High'].iloc[idx] * 1.02
                    
            marker = '^' if is_bullish else 'v'
            color = 'lime' if is_bullish else 'fuchsia'
            addplots.append(mpf.make_addplot(marker_series, type='scatter', markersize=120, marker=marker, color=color))

        # Dynamic panel ratios (Main Price : Volume : RSI)
        panel_ratios = (5, 2, 2) if is_momentum else (7, 3)

        try:
            mpf.plot(
                plot_df,
                type='candle',
                volume=True,
                style='mike', # Switched to a dark theme for better neon visibility
                title=title_str,
                hlines=hlines,
                fill_between=fill_between,
                addplot=addplots,
                panel_ratios=panel_ratios,
                savefig=dict(fname=str(filepath), dpi=150, bbox_inches='tight', facecolor='#1e1e1e'),
                warn_too_much_data=100
            )
            logger.debug(f"Generated chart graphic: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Failed to generate chart for {ticker}: {e}", exc_info=True)
            return ""