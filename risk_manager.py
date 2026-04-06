"""
ShieldTrade-CE — Risk Management Module (V5.2.1)
=================================================
Provides:
  - DrawdownGuard: Pauses the bot if equity drops beyond threshold
  - ATR computation: Adaptive volatility measurement
  - Emergency Stop-Loss: ATR-based loss cutting
  - Volume Confirmation: Validates entry signals with volume
"""

import pandas as pd
import numpy as np
from datetime import datetime


class DrawdownGuard:
    """
    Monitors portfolio equity and halts trading if the maximum
    drawdown threshold is breached.

    Usage:
        guard = DrawdownGuard(max_drawdown_pct=0.15)
        should_stop, dd = guard.update(current_equity)
        if should_stop:
            send_telegram("🛑 DRAWDOWN LIMIT HIT — BOT PAUSED")
    """

    def __init__(self, max_drawdown_pct=0.15):
        self.max_dd = max_drawdown_pct
        self.peak_equity = 0.0
        self.is_paused = False

    def update(self, current_equity):
        """
        Update with current equity value.
        Returns: (should_pause: bool, current_drawdown: float)
        """
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        if self.peak_equity == 0:
            return False, 0.0

        drawdown = (self.peak_equity - current_equity) / self.peak_equity

        if drawdown >= self.max_dd:
            self.is_paused = True
            return True, drawdown

        # Auto-resume if drawdown recovers below 50% of the threshold
        if self.is_paused and drawdown < (self.max_dd * 0.5):
            self.is_paused = False

        return self.is_paused, drawdown

    def get_status(self, current_equity):
        """Returns a status dict for the dashboard/logs."""
        if self.peak_equity == 0:
            return {
                'peak': 0, 'current': current_equity,
                'drawdown_pct': 0.0, 'remaining_buffer_pct': self.max_dd * 100,
                'is_paused': False
            }
        drawdown = (self.peak_equity - current_equity) / self.peak_equity
        return {
            'peak': round(self.peak_equity, 2),
            'current': round(current_equity, 2),
            'drawdown_pct': round(drawdown * 100, 2),
            'remaining_buffer_pct': round((self.max_dd - drawdown) * 100, 2),
            'is_paused': self.is_paused
        }

    def set_peak(self, peak_value):
        """Restore peak from saved state."""
        self.peak_equity = peak_value


def compute_atr(df, period=14):
    """
    Compute Average True Range (ATR).

    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: ATR lookback window (default 14)

    Returns:
        ATR Series
    """
    high = df['high']
    low = df['low']
    close = df['close']

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(window=period).mean()
    return atr


def compute_atr_trailing_stop(df, period=14, multiplier=2.0):
    """
    Chandelier Exit — dynamic trailing stop based on ATR.

    Returns:
        (trailing_stop_price, current_atr_value)
    """
    atr = compute_atr(df, period)
    current_atr = atr.iloc[-1]

    if pd.isna(current_atr) or current_atr == 0:
        return 0, 0

    highest = df['close'].rolling(window=period).max()
    trailing_stop = highest.iloc[-1] - (current_atr * multiplier)

    return trailing_stop, current_atr


def check_emergency_stop(avg_price, current_price, current_atr, multiplier=3.0):
    """
    Emergency stop-loss: triggers when price falls more than
    multiplier × ATR below the average entry price.

    Returns:
        (should_stop: bool, stop_price: float)
    """
    if current_atr == 0 or avg_price == 0:
        return False, 0

    stop_price = avg_price - (current_atr * multiplier)

    if current_price <= stop_price:
        return True, stop_price

    return False, stop_price


def volume_confirmation(df, period=20, threshold=1.5):
    """
    Confirms a signal only if current volume exceeds
    threshold × average volume.

    Returns:
        (is_confirmed: bool, volume_ratio: float)
    """
    avg_vol = df['vol'].rolling(window=period).mean()

    if pd.isna(avg_vol.iloc[-1]) or avg_vol.iloc[-1] == 0:
        return True, 0  # Default to True if no data (don't block)

    current_vol = df['vol'].iloc[-1]
    volume_ratio = current_vol / avg_vol.iloc[-1]

    return volume_ratio >= threshold, round(volume_ratio, 2)
