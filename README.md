# 🛡️ ShieldTrade-CE (Community Edition)
**A high-performance, non-custodial trading bot for SOL and ETH with Trailing Take Profit and secure mobile dashboard.**

---

## 🌟 Overview
ShieldTrade-CE is an Open Source trading bot designed to run on personal hardware. It focuses on capital preservation and profit optimization using advanced DCA (Dollar Cost Averaging) strategies and a Dynamic Trailing Take Profit (TTP) engine.

## ⚙️  Configuration & Modes
The bot is designed to be flexible and safe for beginners:
* **Hybrid Execution Mode:** Controlled by the `SIMULATION` toggle in `main.py`.
    * `True`: (Default) Runs with virtual funds for risk-free strategy testing.
    * `False`: Connects to Binance API for live trading.
* **Real-time Notifications:** Uses Telegram Bot API to send instant alerts for every buy, sell, and trailing update.

## 🟠 Current Status: V5.2 Simons Edition

**Update Date:** April 6, 2026.
The bot is currently running in a **live environment** with a liquid capital of **60.72 USDC**.
* **Algorithmic Core:** Successfully transitioned to **Mean Reversion Statistical Arbitrage**. The bot is currently in "Statistical Stalking" mode.
* **Execution Metrics:**
    * **Z-Score Entry Threshold:** Set at $-2.0\sigma$.
    * **Cycle Frequency:** 10 seconds.
    * **Market Status:** Neutral/Bullish (Current $\sigma$ levels ranging between +0.5 and +1.3).
* **Next Milestone:** Monitor entry behavior under the new statistical model before deploying Variable TTP (V5.3).

## 🛠️  Setup Instructions
1. **Telegram Setup:** Create a bot via [@BotFather](https://t.me/botfather) and retrieve your `TELEGRAM_TOKEN` and `CHAT_ID`.
2. **API Keys:** If using Live Mode, generate API keys on Binance with "Spot Trading" enabled (Withdrawals MUST be disabled).
3. **Run with Docker:**
   ```bash
   docker build -t shieldtrade-bot .
   docker run -d --name trading-bot shieldtrade-bot
   ```

## 🚀 Key Features
* **Statistical Z-Score Entry (V5.2):** Quantitative logic that eliminates RSI noise. It triggers only when the price deviates -2.0 standard deviations from the mean (Gaussian Distribution).
* **Titanium Real-Time Sync:** High-frequency balance reconciliation. The bot queries the actual "free" USDC balance from Binance every 10 seconds to eliminate data drift.
* **Quant-Style Dashboard:** Real-time monitoring of Sigma ($\sigma$) levels and market overextension via a dedicated Web App.
* **HTF Trend Filtering:** EMA 200 (1H) macro-filter ensures entries only occur with institutional bullish momentum support.
* **Anti-Dust Pro:** Guaranteed 100% position clearing. It queries real-time exchange holdings before every sell execution to prevent leftover "dust" in the portfolio.
* **Compound Interest Scaling:** Autonomous management of up to 6 slots, auto-adjusting position size as the capital grows.

## 🏗️ Architecture
1. Bot Core: Python engine (CCXT) managing logic and market interaction.
2. API Backend: FastAPI serving the internal state and equity history.
3. Frontend: Real-time web dashboard (Tailwind CSS + Chart.js).

## 🛡️  Security First
* **Zero-Trust:** Designed to be exposed via Cloudflare Tunnels with secondary password authentication.
* **Safety Net:** Built-in balance checks before any trade execution.
* **Withdrawal Protection:** Users are encouraged to disable "Withdrawal" permissions on their API keys.

## 🗺️ Roadmap ShieldTrade-CE
- 🟢 **V3.3:** Shielded logic and rotative logs.
- 🟢 **V4.0:** Implementation of the Trailing Take Profit engine (TTP).
- 🟢 **V4.1:** HTF Trend Filter (EMA 200 1H) to avoid "falling knives".
- 🟢 **V4.2:** Data Integrity: Atomic JSON storage and Auto-Reconciliation.
- 🟢 **V4.2.1:** Production Milestone: First deployment with real capital (50€ Test).
- 🟢 **V4.3:** TTP Optimization: Calibration of dynamic trailing gaps.
- 🟢 **V4.4:** Backend Core: Integration with FastAPI for monitoring.
- 🟢 **V5.0:** User Experience: First Web App deployment.
- 🟢 **V5.1:** **Scalable Code & Titanium Sync:** Multi-slot logic and real-time balance reconciliation.
- 🟠 **V5.1.1:** Easy install / Docker-compose orchestration.
- 🟠 **V5.2:** **Z-Score Entry:** Transition from RSI to Standard Deviation logic ($Z < -2.0\sigma$).
- ⚪ **V5.3:** **Variable TTP:** Implementation of dynamic Trailing Stop based on ATR (Average True Range).
- ⚪ **V5.4:** **Bitcoin Shield:** Correlation-based panic filter monitoring BTC health to block altcoin entries during crashes.
- ⚪ **V6.0:** **Elite Management:** Integration of the Kelly Criterion for risk sizing and Limit Orders (Maker) for fee optimization.

## 🎓 Maintenance & Development Status
**ShieldTrade-CE** is a personal learning project and a hobby.

* **Academic Priority:** The lead developer is a university student. Academic responsibilities are the top priority, so development happens exclusively during free time.
* **Release Cycle:** There is no fixed schedule for updates. New features or versions will be released as time and studies permit.
* **Expectations:** While I am passionate about this project, please understand that responses to issues or pull requests may be delayed.

*This project is a journey of learning and experimentation, not a commercial product.*

## ⚠️ Disclaimer (DYOR)
**Trading cryptocurrencies involves significant risk.** ShieldTrade-CE is an experimental tool provided "as is" for educational purposes. By using this software, you acknowledge that:

1. **Risk of Loss:** You can lose some or all of your capital. Crypto markets are highly volatile (**Risk level: 6/6**).
2. **No Financial Advice:** The authors of this project are not financial advisors. This bot's logic is not a guarantee of profit.
3. **Personal Responsibility:** You are solely responsible for your API keys, funds, and any trading decisions made by the bot.
4. **DYOR:** Do Your Own Research before deploying real capital. Always test in **Simulation Mode** first.

*The authors shall not be held liable for any financial losses or damages resulting from the use of this software.

## 🤝 Contributing
Contributions are what make the open-source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📈 Monetization & Support
This project is Open Source. If you want to support development:
* **Save 10% on Trading Fees:** Register on Binance using our [Referral Link](https://www.binance.com/activity/referral-entry/CPA?ref=CPA_000RJSUT5M).
