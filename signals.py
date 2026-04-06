"""
ShieldTrade-CE — Signals Module (V5.2.1)
=========================================
Provides:
  - VolatilityRegime: Detects market regime and adapts parameters
  - Bollinger Squeeze: Detects volatility compression breakouts
  - StochRSI: Stochastic RSI for oversold confirmation
  - BTC Shield: Correlation-based altcoin entry blocker
"""

import pandas as pd
import numpy as np
from risk_manager import compute_atr


class VolatilityRegime:
    """
    Classifies the market into 3 regimes (low / medium / high volatility)
    using ATR percentiles, and returns adapted trading parameters.

    Usage:
        regime = VolatilityRegime()
        name, params = regime.detect(df)
        z_entry = params['z_entry']
        profit_margin = params['profit_margin']
        atr_mult = params['atr_mult']
    """

    def __init__(self):
        self.regimes = {
            'low': {
                'z_entry': -1.5,
                'profit_margin': 0.01,
                'atr_mult': 1.5,
                'dca_drop': 0.02,
            },
            'medium': {
                'z_entry': -2.0,
                'profit_margin': 0.015,
                'atr_mult': 2.0,
                'dca_drop': 0.03,
            },
            'high': {
                'z_entry': -2.5,
                'profit_margin': 0.025,
                'atr_mult': 3.0,
                'dca_drop': 0.05,
            },
        }

    def detect(self, df, atr_period=14, lookback=200):
        """
        Detect current volatility regime.

        Returns:
            (regime_name: str, params: dict)
        """
        atr = compute_atr(df, atr_period)
        current_atr = atr.iloc[-1]

        if pd.isna(current_atr):
            return 'medium', self.regimes['medium']

        # Compute the percentile of the current ATR vs. history
        atr_tail = atr.tail(lookback).dropna()
        if len(atr_tail) < 10:
            return 'medium', self.regimes['medium']

        atr_percentile = (atr_tail < current_atr).sum() / len(atr_tail)

        if atr_percentile < 0.33:
            return 'low', self.regimes['low']
        elif atr_percentile < 0.66:
            return 'medium', self.regimes['medium']
        else:
            return 'high', self.regimes['high']


def bollinger_squeeze(df, period=20, k=2.0, squeeze_lookback=100, squeeze_percentile=0.05):
    """
    Detect Bollinger Squeeze (low bandwidth precedes big moves).

    Returns:
        (is_squeeze: bool, pct_b: float, bbw: float)
        - is_squeeze: True if bands are at historical minimum width
        - pct_b: %B indicator (0 = lower band, 1 = upper band)
        - bbw: Band Width (normalized)
    """
    sma = df['close'].rolling(period).mean()
    std = df['close'].rolling(period).std()

    upper = sma + k * std
    lower = sma - k * std

    bbw = (upper - lower) / sma
    pct_b = (df['close'] - lower) / (upper - lower)

    # Check if current BBW is in the lowest percentile
    bbw_tail = bbw.tail(squeeze_lookback).dropna()
    if len(bbw_tail) < 10:
        return False, 0, 0

    current_bbw = bbw.iloc[-1]
    bbw_rank = (bbw_tail < current_bbw).sum() / len(bbw_tail)

    is_squeeze = bbw_rank < squeeze_percentile
    return is_squeeze, round(pct_b.iloc[-1], 3), round(current_bbw, 5)


def stoch_rsi(df, rsi_period=14, stoch_period=14, k_smooth=3, d_smooth=3):
    """
    Stochastic RSI — combines RSI sensitivity with stochastic oscillator.

    Returns:
        (is_oversold_crossover: bool, k_value: float, d_value: float)
    """
    # Compute RSI manually
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=rsi_period).mean()
    avg_loss = loss.rolling(window=rsi_period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    # Stochastic of RSI
    rsi_min = rsi.rolling(window=stoch_period).min()
    rsi_max = rsi.rolling(window=stoch_period).max()
    rsi_range = rsi_max - rsi_min

    stoch = (rsi - rsi_min) / rsi_range.replace(0, np.nan)

    k_line = stoch.rolling(window=k_smooth).mean()
    d_line = k_line.rolling(window=d_smooth).mean()

    k_val = k_line.iloc[-1]
    d_val = d_line.iloc[-1]

    if pd.isna(k_val) or pd.isna(d_val):
        return False, 0, 0

    # Bullish crossover in oversold zone
    crossover = (k_line.iloc[-1] > d_line.iloc[-1]) and \
                (k_line.iloc[-2] <= d_line.iloc[-2])
    oversold = k_val < 0.20

    return crossover and oversold, round(k_val, 3), round(d_val, 3)


def macd_divergence(df, fast=12, slow=26, signal_period=9, lookback=20):
    """
    Detect bullish MACD histogram divergence:
    Price makes a lower low, but MACD histogram makes a higher low.

    Returns:
        (is_bullish_divergence: bool, histogram_value: float)
    """
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal_period, adjust=False).mean()
    histogram = macd - macd_signal

    if len(df) < lookback + 2:
        return False, 0

    recent_price = df['close'].tail(lookback)
    recent_hist = histogram.tail(lookback)

    # Price makes lower low
    price_lower_low = recent_price.iloc[-1] < recent_price.min()

    # Histogram makes higher low (momentum improving despite price drop)
    hist_min_prev = recent_hist.iloc[:-1].min()
    hist_current = recent_hist.iloc[-1]
    hist_higher_low = hist_current > hist_min_prev

    is_divergence = price_lower_low and hist_higher_low
    return is_divergence, round(histogram.iloc[-1], 6)


def btc_shield(exchange, correlation_window=48, btc_drop_threshold=-0.05):
    """
    BTC Shield — blocks altcoin entries when BTC is in panic mode.

    Checks:
      1. BTC 4-hour price change
      2. BTC daily relative performance

    Returns:
        (is_safe: bool, reason: str)
    """
    try:
        btc_ohlcv = exchange.fetch_ohlcv('BTC/USDC', '1h', limit=correlation_window + 5)
        btc_df = pd.DataFrame(btc_ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])

        if len(btc_df) < 5:
            return True, "⚠️ BTC: Datos insuficientes, permitiendo entrada"

        # BTC change over last 4 hours
        btc_now = btc_df['close'].iloc[-1]
        btc_4h_ago = btc_df['close'].iloc[-5] if len(btc_df) >= 5 else btc_now
        btc_change_4h = (btc_now - btc_4h_ago) / btc_4h_ago

        # BTC change over last 24 hours
        btc_24h_ago = btc_df['close'].iloc[0] if len(btc_df) >= 24 else btc_now
        btc_change_24h = (btc_now - btc_24h_ago) / btc_24h_ago

        # Block if BTC dropped more than threshold in 4h
        if btc_change_4h < btc_drop_threshold:
            return False, f"🛑 BTC Shield: BTC 4h={btc_change_4h:+.2%} (umbral: {btc_drop_threshold:.1%})"

        # Warning if BTC dropped significantly in 24h
        if btc_change_24h < (btc_drop_threshold * 2):
            return False, f"🛑 BTC Shield: BTC 24h={btc_change_24h:+.2%} (caída severa)"

        return True, f"✅ BTC OK: 4h={btc_change_4h:+.2%}, 24h={btc_change_24h:+.2%}"

    except Exception as e:
        return True, f"⚠️ BTC Shield error: {e} (permitiendo entrada)"


def compute_entry_score(df, price, z_score, has_volume, is_squeeze, is_stoch_oversold, is_macd_div):
    """
    Multi-signal consensus scoring system.
    Requires minimum 3/5 signals to trigger entry.

    Returns:
        (should_enter: bool, score: int, details: str)
    """
    score = 0
    signals = []

    # 1. Z-Score (weight: 2)
    if z_score < -2.0:
        score += 2
        signals.append(f"Z-Score={z_score}σ ✅✅")
    elif z_score < -1.5:
        score += 1
        signals.append(f"Z-Score={z_score}σ ✅")

    # 2. Volume Confirmation (weight: 1)
    if has_volume:
        score += 1
        signals.append("Volume ✅")

    # 3. Bollinger Squeeze (recent compression → breakout likely) (weight: 1)
    if is_squeeze:
        score += 1
        signals.append("Squeeze ✅")

    # 4. StochRSI oversold crossover (weight: 1)
    if is_stoch_oversold:
        score += 1
        signals.append("StochRSI ✅")

    # 5. MACD Divergence (weight: 1)
    if is_macd_div:
        score += 1
        signals.append("MACD Div ✅")

    max_score = 6  # 2 + 1 + 1 + 1 + 1
    threshold = 3

    details = f"Score: {score}/{max_score} | " + ", ".join(signals)
    return score >= threshold, score, details
