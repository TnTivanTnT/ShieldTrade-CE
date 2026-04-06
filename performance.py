"""
ShieldTrade-CE — Performance Tracking Module (V5.2.1)
=====================================================
Provides:
  - PerformanceTracker: Computes Sharpe, Sortino, Profit Factor,
    Win Rate, Max Drawdown, and Expectancy from trade history.
  - Kelly Criterion: Optimal position sizing based on historical performance.
"""

import json
import os
import numpy as np
from datetime import datetime


class PerformanceTracker:
    """
    Tracks all completed trades and computes quantitative performance metrics.

    Usage:
        tracker = PerformanceTracker("/app/performance.json")
        tracker.add_trade('SOL/USDC', 'BUY', 120.5, 0.12, 14.46)
        tracker.add_trade('SOL/USDC', 'SELL', 122.3, 0.12, 14.68, profit=0.22)
        metrics = tracker.compute_metrics()
    """

    SAVE_PATH = "/app/performance.json"

    def __init__(self, save_path=None):
        if save_path:
            self.SAVE_PATH = save_path
        self.trades = []
        self.equity_snapshots = []
        self.load()

    def load(self):
        """Load trade history from disk."""
        if os.path.exists(self.SAVE_PATH):
            try:
                with open(self.SAVE_PATH, 'r') as f:
                    data = json.load(f)
                    self.trades = data.get('trades', [])
                    self.equity_snapshots = data.get('equity_snapshots', [])
            except Exception:
                self.trades = []
                self.equity_snapshots = []

    def save(self):
        """Persist trade history to disk."""
        try:
            data = {
                'trades': self.trades,
                'equity_snapshots': self.equity_snapshots,
                'last_updated': datetime.now().isoformat()
            }
            tmp_path = self.SAVE_PATH + ".tmp"
            with open(tmp_path, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.SAVE_PATH)
        except Exception as e:
            print(f"[PERF] Error saving performance data: {e}")

    def add_trade(self, pair, side, price, amount, cost, profit=None):
        """Record a completed trade."""
        self.trades.append({
            'timestamp': datetime.now().isoformat(),
            'pair': pair,
            'side': side,
            'price': price,
            'amount': amount,
            'cost': cost,
            'profit': profit  # Only for SELL trades
        })
        self.save()

    def add_equity_snapshot(self, equity):
        """Record current equity for Sharpe/Sortino calculation."""
        self.equity_snapshots.append({
            'timestamp': datetime.now().isoformat(),
            'equity': equity
        })
        # Keep only last 1000 snapshots
        if len(self.equity_snapshots) > 1000:
            self.equity_snapshots = self.equity_snapshots[-1000:]
        self.save()

    def compute_metrics(self):
        """
        Compute all performance metrics from trade history.

        Returns a dict with:
            total_trades, win_rate, avg_win, avg_loss,
            profit_factor, sharpe_ratio, sortino_ratio,
            max_drawdown, expectancy, kelly_fraction
        """
        sell_trades = [t for t in self.trades if t['side'] == 'SELL' and t['profit'] is not None]

        if len(sell_trades) < 2:
            return {
                'total_trades': len(sell_trades),
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'sharpe_ratio': 0,
                'sortino_ratio': 0,
                'max_drawdown_pct': 0,
                'expectancy': 0,
                'kelly_fraction': 0.02,  # Default conservative
                'status': 'insufficient_data'
            }

        profits = [t['profit'] for t in sell_trades]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p <= 0]

        total = len(profits)
        win_rate = (len(wins) / total) * 100 if total > 0 else 0

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        # Profit Factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0.01  # Avoid division by zero
        profit_factor = gross_profit / gross_loss

        # Expectancy (average profit per trade)
        expectancy = sum(profits) / total

        # Sharpe Ratio (annualized, assuming 1 trade per day avg)
        profits_arr = np.array(profits)
        if profits_arr.std() > 0:
            sharpe = (profits_arr.mean() / profits_arr.std()) * np.sqrt(365)
        else:
            sharpe = 0

        # Sortino Ratio (only penalizes downside volatility)
        downside = profits_arr[profits_arr < 0]
        if len(downside) > 0 and downside.std() > 0:
            sortino = (profits_arr.mean() / downside.std()) * np.sqrt(365)
        else:
            sortino = 0

        # Max Drawdown from equity snapshots
        max_dd_pct = self._compute_max_drawdown()

        # Kelly Criterion
        kelly = self._compute_kelly(win_rate / 100, avg_win, abs(avg_loss))

        return {
            'total_trades': total,
            'win_rate': round(win_rate, 1),
            'avg_win': round(avg_win, 4),
            'avg_loss': round(avg_loss, 4),
            'profit_factor': round(profit_factor, 2),
            'sharpe_ratio': round(sharpe, 2),
            'sortino_ratio': round(sortino, 2),
            'max_drawdown_pct': round(max_dd_pct, 2),
            'expectancy': round(expectancy, 4),
            'kelly_fraction': round(kelly, 4),
            'status': 'ok'
        }

    def _compute_max_drawdown(self):
        """Compute max drawdown percentage from equity snapshots."""
        if len(self.equity_snapshots) < 2:
            return 0

        equities = np.array([s['equity'] for s in self.equity_snapshots])
        peak = np.maximum.accumulate(equities)
        drawdowns = (peak - equities) / peak
        return float(drawdowns.max()) * 100

    def _compute_kelly(self, win_rate, avg_win, avg_loss):
        """
        Kelly Criterion — optimal fraction of capital to risk.
        Returns Half-Kelly for safety, clamped to [0.5%, 5%].
        """
        if avg_loss == 0 or win_rate == 0:
            return 0.02  # Default 2%

        b = avg_win / avg_loss  # Win/loss ratio
        q = 1 - win_rate  # Loss rate

        kelly = (win_rate * b - q) / b

        # Half-Kelly for safety
        half_kelly = kelly / 2

        # Guardrails
        return max(0.005, min(half_kelly, 0.05))

    def get_position_size(self, total_capital):
        """Get recommended position size based on Kelly Criterion."""
        metrics = self.compute_metrics()
        fraction = metrics['kelly_fraction']
        return round(total_capital * fraction, 2)

    def format_report(self):
        """Generate a formatted Telegram-ready performance report."""
        m = self.compute_metrics()
        if m['status'] == 'insufficient_data':
            return "📊 Performance: Insuficientes trades para análisis"

        return (
            f"📊 **Performance Report**\n"
            f"Trades: {m['total_trades']} | Win Rate: {m['win_rate']}%\n"
            f"Avg Win: {m['avg_win']:.4f}$ | Avg Loss: {m['avg_loss']:.4f}$\n"
            f"Profit Factor: {m['profit_factor']} | Expectancy: {m['expectancy']:.4f}$\n"
            f"Sharpe: {m['sharpe_ratio']} | Sortino: {m['sortino_ratio']}\n"
            f"Max DD: {m['max_drawdown_pct']:.1f}% | Kelly: {m['kelly_fraction']*100:.1f}%"
        )
