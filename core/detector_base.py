from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Any

class PatternDetector(ABC):
    """
    Abstract Base Class for all technical pattern detectors.
    Enforces a strict contract for scanning price data and returning standardized signal data.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """The formal name of the detector (e.g., 'Fair Value Gap')."""
        pass

    @abstractmethod
    def scan(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Scans a DataFrame for the specific technical setup.
        
        Args:
            df (pd.DataFrame): OHLCV data. Expected columns: Open, High, Low, Close, Volume.
            
        Returns:
            Dict[str, Any]: Standardized output. Must contain at minimum:
                - 'signal_found' (bool): True if the setup meets strict criteria.
                - 'setup_type' (str): e.g., 'Bullish' or 'Bearish'.
                - 'quality_score' (float): 0.0 to 1.0 representing confluence/strength.
                - 'trigger_price' (float): The price level of interest.
                - 'invalidation_level' (float): The structural failure point (for manual SL placement).
                - 'metadata' (Dict): Specific metrics for journaling (e.g., gap size, volume surge).
        """
        pass