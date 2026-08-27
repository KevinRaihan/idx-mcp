"""Predictions module for trading evaluation tools.

Provides tools for calculating Expected Value (EV), retrieving trade setups,
fetching IDX news, and logging prediction snapshots for forward testing.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import httpx
import yfinance as yf
import pandas as pd

from ..utils.ticker import to_yfinance_ticker, validate_ticker

logger = logging.getLogger("idx-mcp.tools.predictions")

# Project root logs directory
LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
PREDICTIONS_LOG_FILE = LOGS_DIR / "predictions_log.json"


async def get_trade_setup(ticker: str, lookback_days: int = 60) -> dict:
    """Fetch OHLCV data, 20/50 SMA, and 14-day ATR for a given ticker."""
    try:
        normalized = validate_ticker(ticker)
        yf_ticker = to_yfinance_ticker(normalized)
    except ValueError as e:
        return {"error": True, "message": str(e)}

    # Ensure we have enough data for 50 SMA and 14 ATR
    fetch_period = "6mo" if lookback_days <= 100 else "1y"

    try:
        stock = yf.Ticker(yf_ticker)
        # Run synchronous yfinance fetch in a thread
        hist = await asyncio.to_thread(lambda: stock.history(period=fetch_period))
        
        if hist.empty:
            return {"error": True, "message": f"No data found for {normalized}."}

        # Take only the needed lookback for the raw OHLCV return, but calculate SMA/ATR on full fetched data
        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]

        # Calculate SMA
        sma_20 = close.rolling(window=20).mean()
        sma_50 = close.rolling(window=50).mean()

        # Calculate ATR 14
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr_14 = tr.ewm(alpha=1 / 14, adjust=False).mean()

        # Get latest values
        current_price = float(close.iloc[-1])
        latest_sma_20 = float(sma_20.iloc[-1]) if len(sma_20.dropna()) > 0 else None
        latest_sma_50 = float(sma_50.iloc[-1]) if len(sma_50.dropna()) > 0 else None
        latest_atr_14 = float(atr_14.iloc[-1]) if len(atr_14.dropna()) > 0 else None

        # Calculate dynamic support/resistance barriers using ATR
        support = current_price - (latest_atr_14 * 2) if latest_atr_14 else None
        resistance = current_price + (latest_atr_14 * 2) if latest_atr_14 else None

        # Filter the history to requested lookback days for OHLCV
        recent_hist = hist.tail(lookback_days).copy()
        recent_hist.index = recent_hist.index.astype(str)
        
        ohlcv = []
        for date, row in recent_hist.iterrows():
            ohlcv.append({
                "date": date.split(" ")[0], # Keep YYYY-MM-DD
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            })

        return {
            "ticker": normalized,
            "current_price": current_price,
            "sma_20": latest_sma_20,
            "sma_50": latest_sma_50,
            "atr_14": latest_atr_14,
            "support_barrier": support,
            "resistance_barrier": resistance,
            "ohlcv_data": ohlcv,
        }
    except Exception as e:
        logger.exception(f"Error fetching trade setup for {normalized}")
        return {"error": True, "message": f"Failed to fetch trade setup: {str(e)}"}


async def fetch_idx_news(ticker: str, max_articles: int = 5) -> dict:
    """Fetch latest news for a company using yfinance, falling back to Google News RSS."""
    try:
        normalized = validate_ticker(ticker)
        yf_ticker = to_yfinance_ticker(normalized)
    except ValueError as e:
        return {"error": True, "message": str(e)}

    articles = []
    
    # 1. Try yfinance news
    try:
        stock = yf.Ticker(yf_ticker)
        yf_news = await asyncio.to_thread(lambda: stock.news)
        
        for item in yf_news:
            if len(articles) >= max_articles:
                break
            articles.append({
                "title": item.get("title"),
                "publisher": item.get("publisher"),
                "publish_time": item.get("providerPublishTime"), # Unix timestamp
                "link": item.get("link"),
                "source": "yfinance"
            })
    except Exception as e:
        logger.warning(f"Failed to fetch yfinance news for {normalized}: {e}")

    # 2. Fallback to Google News RSS if sparse
    if len(articles) < max_articles:
        try:
            rss_url = f"https://news.google.com/rss/search?q=saham+{normalized}&hl=id&gl=ID&ceid=ID:id"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(rss_url)
                
            if response.status_code == 200:
                root = ET.fromstring(response.text)
                channel = root.find("channel")
                if channel is not None:
                    for item in channel.findall("item"):
                        if len(articles) >= max_articles:
                            break
                        
                        # Parse publication date to unix timestamp approximation if possible
                        pub_date = item.findtext("pubDate")
                        
                        articles.append({
                            "title": item.findtext("title"),
                            "publisher": item.findtext("source"),
                            "publish_time": pub_date,
                            "link": item.findtext("link"),
                            "source": "google_news"
                        })
        except Exception as e:
            logger.warning(f"Failed to fetch Google News RSS for {normalized}: {e}")

    return {
        "ticker": normalized,
        "articles": articles
    }


async def calculate_expected_value(
    win_prob: float, 
    profit_target_idr: float, 
    loss_target_idr: float, 
    buy_fee_rate: float = 0.0015, 
    sell_fee_rate: float = 0.0025
) -> dict:
    """Calculate deterministic Expected Value accounting for IDX friction."""
    try:
        if not (0.0 <= win_prob <= 1.0):
            return {"error": True, "message": "win_prob must be between 0.0 and 1.0"}

        # Friction accounting
        net_win = profit_target_idr - (profit_target_idr * sell_fee_rate) - (loss_target_idr * buy_fee_rate)
        net_loss = loss_target_idr + (loss_target_idr * buy_fee_rate) + (loss_target_idr * sell_fee_rate)
        
        # Expected value formula
        ev = (win_prob * net_win) - ((1.0 - win_prob) * net_loss)
        
        return {
            "ev_idr": ev,
            "net_win_idr": net_win,
            "net_loss_idr": net_loss,
            "win_prob": win_prob,
            "buy_fee_rate": buy_fee_rate,
            "sell_fee_rate": sell_fee_rate
        }
    except Exception as e:
        return {"error": True, "message": f"Failed to calculate EV: {str(e)}"}


async def log_prediction_snapshot(
    ticker: str, 
    initial_ev: float, 
    ai_win_prob: float, 
    reasoning: str, 
    target_date: str,
    strategy_name: str
) -> dict:
    """Append the AI agent's trade thesis into a structured predictions_log.json file."""
    try:
        normalized = validate_ticker(ticker)
    except ValueError as e:
        return {"error": True, "message": str(e)}

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": normalized,
        "strategy": strategy_name,
        "initial_ev": initial_ev,
        "ai_win_prob": ai_win_prob,
        "reasoning": reasoning,
        "target_date": target_date
    }

    try:
        # Read existing
        logs = []
        if PREDICTIONS_LOG_FILE.exists():
            with open(PREDICTIONS_LOG_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    logs = json.loads(content)
        
        # Append
        logs.append(snapshot)
        
        # Write back
        with open(PREDICTIONS_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)
            
        return {
            "success": True, 
            "message": f"Prediction snapshot for {normalized} successfully logged.",
            "timestamp": snapshot["timestamp"]
        }
    except Exception as e:
        logger.exception("Failed to write prediction snapshot.")
        return {"error": True, "message": f"Failed to log prediction: {str(e)}"}


async def gather_intelligence(ticker: str, lookback_days: int = 60, max_articles: int = 5) -> dict:
    """Unified tool to fetch trade setup and recent news in a single call."""
    setup_task = get_trade_setup(ticker, lookback_days)
    news_task = fetch_idx_news(ticker, max_articles)
    
    setup_result, news_result = await asyncio.gather(setup_task, news_task)
    
    return {
        "trade_setup": setup_result,
        "news_catalysts": news_result,
        "workflow_instruction": "Evaluate the setup and news to determine win_prob. Then call evaluate_and_log_thesis."
    }


async def evaluate_and_log_thesis(
    ticker: str,
    win_prob: float,
    profit_target_idr: float,
    loss_target_idr: float,
    reasoning: str,
    target_date: str,
    strategy_name: str,
    buy_fee_rate: float = 0.0015,
    sell_fee_rate: float = 0.0025
) -> dict:
    """Calculates EV and logs the prediction snapshot if EV is favorable."""
    ev_result = await calculate_expected_value(
        win_prob, profit_target_idr, loss_target_idr, buy_fee_rate, sell_fee_rate
    )
    
    if ev_result.get("error"):
        return ev_result
        
    log_result = await log_prediction_snapshot(
        ticker, ev_result["ev_idr"], win_prob, reasoning, target_date, strategy_name
    )
    
    return {
        "expected_value_analysis": ev_result,
        "logging_status": log_result
    }
