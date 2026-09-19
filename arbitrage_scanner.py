# -*- coding: utf-8 -*-
"""
Automated Funding Rate & Arbitrage Scanner (Free Edition V2.0)
-------------------------------------------------------------------
Scans perpetual futures funding rates for major crypto pairs (BTC, ETH, SOL, XRP, ADA)
via CCXT, identifies annualized yield (APY) opportunities, enforces spot market parity, 
and dispatches real-time alerts with clean TradingView chart links to Telegram.
"""

import os
import requests
import ccxt
import pandas as pd

# Safe fallback if config.py is missing or incomplete
try:
    import config
except ImportError:
    config = None

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    token = TELEGRAM_BOT_TOKEN.strip() if TELEGRAM_BOT_TOKEN else None
    chat_id = TELEGRAM_CHAT_ID.strip() if TELEGRAM_CHAT_ID else None

    if not token or not chat_id:
        print("⚠️ Telegram BOT_TOKEN or CHAT_ID missing in environment variables. Notification skipped.")
        return
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        
        # If Telegram rejects HTML formatting (HTTP 400), retry sending as plain text
        if response.status_code == 400:
            print("⚠️ HTML formatting rejected by Telegram (HTTP 400). Retrying as plain text...")
            payload.pop("parse_mode", None)
            clean_text = message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<a>", "").replace("</a>", "")
            payload["text"] = clean_text
            response = requests.post(url, json=payload, timeout=10)
            
        if response.status_code == 200:
            print("🚀 Telegram alert delivered successfully!")
        else:
            print(f"❌ Telegram API Error ({response.status_code}): {response.text}")
            
    except Exception as e:
        print(f"❌ Network/Telegram Error: {e}")

def scan_funding_opportunities():
    exchange_name = getattr(config, 'EXCHANGE_ID', 'kraken').lower() if config else 'kraken'
    min_apy = getattr(config, 'MIN_APY_THRESHOLD', 10.0) if config else 10.0
    max_apy_cap = getattr(config, 'MAX_APY_CAP', 200.0) if config else 200.0
    est_fees = getattr(config, 'ESTIMATED_FEES_PCT', 0.50) if config else 0.50
    require_spot = getattr(config, 'REQUIRE_SPOT_PARITY', True) if config else True

    # Free edition target pairs: Focus on core liquid markets including XRP
    TARGET_ASSETS = {'BTC', 'ETH', 'SOL', 'XRP', 'ADA'}

    # Minimum 24h trading volume threshold in USD
    MIN_VOLUME_USD = getattr(config, 'MIN_VOLUME_USD', 100000.0) if config else 100000.0

    print(f"🔎 Initializing Free Funding Rate Scanner for {exchange_name.upper()} ({', '.join(TARGET_ASSETS)})...")

    try:
        spot_class = getattr(ccxt, exchange_name)
        futures_class = getattr(ccxt, f"{exchange_name}futures", spot_class)
        
        spot = spot_class({'enableRateLimit': True})
        futures = futures_class({'enableRateLimit': True})
        
        spot_markets = spot.load_markets()
        futures_markets = futures.load_markets()
        rates = futures.fetch_funding_rates()
        
        try:
            futures_tickers = futures.fetch_tickers()
        except Exception:
            futures_tickers = {}

    except Exception as e:
        print(f"❌ Error fetching market data from {exchange_name.upper()}: {e}")
        return

    spot_base_currencies = {market['base'] for symbol, market in spot_markets.items() if market.get('base')}
    results = []

    for symbol, data in rates.items():
        market_info = futures_markets.get(symbol, {})
        base_currency = market_info.get('base')
        
        # Filter: Only scan selected core assets in the free edition
        if not base_currency or base_currency.upper() not in TARGET_ASSETS:
            continue
            
        ticker_info = futures_tickers.get(symbol, {})
        volume_usd = ticker_info.get('quoteVolume', 0) or ticker_info.get('baseVolume', 0) or 0
        
        if volume_usd < MIN_VOLUME_USD:
            continue

        rate = data.get('fundingRate')
        if rate is not None:
            has_spot = base_currency in spot_base_currencies
            
            interval_raw = data.get('interval', 8)
            if isinstance(interval_raw, str):
                clean_str = interval_raw.lower().replace('h', '').strip()
                try:
                    interval_hours = int(clean_str)
                except ValueError:
                    interval_hours = 8
            elif isinstance(interval_raw, (int, float)) and interval_raw > 0:
                interval_hours = int(interval_raw)
            else:
                interval_hours = 8

            payments_per_day = 24 / interval_hours
            rate_pct = rate * 100 
            
            annual_apy = abs(rate_pct) * payments_per_day * 365
            
            # Sanity cap filter for anomaly protection
            if annual_apy > max_apy_cap:
                continue

            if rate_pct > 0:
                strategy = "Long Spot + Short Futures"
            else:
                strategy = "Short Spot + Long Futures"

            days_to_breakeven = round(est_fees / (abs(rate_pct) * payments_per_day), 1) if rate_pct != 0 else 'N/A'
            
            clean_symbol = symbol.split(':')[0]
            tv_ticker = clean_symbol.replace('/', '')
            tv_url = f"https://www.tradingview.com/symbols/{tv_ticker}"
            
            results.append({
                'Asset': base_currency,
                'Futures Symbol': clean_symbol,
                'TradingView URL': tv_url,
                'Spot Parity': "YES ✅" if has_spot else "NO ❌",
                'Funding Rate (%)': round(rate_pct, 4),
                'Interval (h)': int(interval_hours),
                'Projected APY (%)': round(annual_apy, 2),
                'Strategy': strategy,
                'Fee Payback (days)': days_to_breakeven,
                'Volume 24h': volume_usd
            })

    df = pd.DataFrame(results)
    if df.empty:
        print("No funding rate data matching filters returned from exchange.")
        return

    if require_spot:
        real_opps = df[(df['Spot Parity'] == "YES ✅") & (df['Projected APY (%)'] >= min_apy)]
    else:
        real_opps = df[df['Projected APY (%)'] >= min_apy]
        
    real_opps = real_opps.sort_values(by='Projected APY (%)', ascending=False)
    
    if not real_opps.empty:
        msg = f"🔥 <b>TOP ARBITRAGE OPPORTUNITIES ({exchange_name.upper()})</b>\n\n"
        for _, row in real_opps.head(5).iterrows():
            clickable_symbol = f'<a href="{row["TradingView URL"]}"><b>{row["Futures Symbol"]}</b></a>'
            
            msg += f"🪙 {clickable_symbol}\n"
            msg += f"├ Strategy: <b>{row['Strategy']}</b>\n"
            msg += f"├ Funding ({row['Interval (h)']}h): <b>{row['Funding Rate (%)']}%</b>\n"
            msg += f"├ APY: <b>+{row['Projected APY (%)']}%</b>\n"
            msg += f"└ Fee Payback: <b>{row['Fee Payback (days)']} days</b>\n\n"
        
        send_telegram_message(msg)
    else:
        print(f"No opportunities found with APY >= {min_apy}%.")

if __name__ == "__main__":
    scan_funding_opportunities()
