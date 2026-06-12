"""Strategy interface for the backtester and live engine."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd


class Strategy(ABC):
    """A strategy looks at candles up to bar i and may emit a trade plan."""

    name: str = "base"

    @abstractmethod
    def on_bar(self, df: pd.DataFrame, i: int) -> Optional[dict]:
        """
        Return None or:
        {"direction": "CE"|"PE", "sl_pct": float, "t1_pct": float,
         "t2_pct": float, "reason": str}
        Percentages are in underlying-move terms used by the backtester.
        """
