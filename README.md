# 🛡️ ShieldTrade-CE (Community Edition)
**A high-performance, non-custodial trading bot for SOL and ETH with Trailing Take Profit and secure mobile dashboard.**

---

## 🌟 Overview
ShieldTrade-CE is an Open Source trading bot designed to run on personal hardware. It focuses on capital preservation and profit optimization using advanced DCA (Dollar Cost Averaging) strategies and a Dynamic Trailing Take Profit (TTP) engine.

## ⚙️ Configuration & Modes
The bot is designed to be flexible and safe for beginners:
* **Hybrid Execution Mode:** Controlled by the `SIMULATION` toggle in `main.py`.
    * `True`: (Default) Runs with virtual funds for risk-free strategy testing.
    * `False`: Connects to Binance API for live trading.
* **Real-time Notifications:** Uses Telegram Bot API to send instant alerts for every buy, sell, and trailing update.

## ⚠️ Current Status: Testing Phase
**Important:** As of February 25, 2026, this bot is in a **Simulation-Only Testing Phase**. 
* The Trailing Take Profit (TTP) and DCA logic have been rigorously tested with virtual data. 
* While the Binance API integration for live trading is implemented, it has not yet been deployed with real capital. Use at your own risk.

## 🛠️ Setup Instructions
1. **Telegram Setup:** Create a bot via [@BotFather](https://t.me/botfather) and retrieve your `TELEGRAM_TOKEN` and `CHAT_ID`.
2. **API Keys:** If using Live Mode, generate API keys on Binance with "Spot Trading" enabled (Withdrawals MUST be disabled).
3. **Run with Docker:**
   ```bash
   docker build -t shieldtrade-bot .
   docker run -d --name trading-bot shieldtrade-bot```
## 🚀 Key Features
* **Dynamic Trailing Take Profit (V4.0):** Tracks market momentum to capture maximum gains beyond the initial 1.5% target.
* **Shielded Execution:** Logic-gate architecture that prevents internal state updates if API orders fail.
* **Non-Custodial & Secure:** API keys are stored locally. No external servers ever touch your credentials.
* **Multi-Pair Support:** Optimized for SOL/EUR and ETH/EUR.
* **Persistent Logging:** 15-day rotative logging system for industrial-grade debugging.

## 🏗️ Architecture
The system consists of two main components:
1.  **Bot Core:** A Python engine running FastAPI that handles all market interactions and strategy execution.
2.  **Mobile Dashboard:** A cross-platform app (Flet/Flutter) that connects to the Core via secure tunnels.

## 🛡️ Security First
* **Zero-Trust:** Designed to be exposed via Cloudflare Tunnels with secondary password authentication.
* **Safety Net:** Built-in balance checks before any trade execution.
* **Withdrawal Protection:** Users are encouraged to disable "Withdrawal" permissions on their API keys.

## 🗺️ Roadmap
- [x] **V3.3:** Shielded logic and rotative logs.
- [ ] **V4.0:** Implementation of the Trailing Take Profit engine (Testing Phase).
- [ ] **V4.1:** Integration with FastAPI for remote monitoring.
- [ ] **V5.0:** Mobile App release and Cloudflare Tunnel support.

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
