"""Predictions module for trading evaluation tools.

Provides tools for calculating Expected Value (EV), retrieving trade setups,
fetching IDX news, and logging prediction snapshots for forward testing.
"""

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import httpx
import yfinance as yf
import pandas as pd

from ..utils.ohlcv import drop_incomplete_bars
from ..utils.paths import predictions_log_file
from ..utils.ticker import to_yfinance_ticker, validate_ticker

logger = logging.getLogger("idx-mcp.tools.predictions")

# Serialises read-modify-write on the predictions log within a single process.
_log_lock = threading.Lock()


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

        # Yahoo appends the live session as a row with volume but all-NaN OHLC;
        # it would poison the SMA/ATR tail and the reported current price.
        hist = drop_incomplete_bars(hist)
        if hist.empty:
            return {"error": True, "message": f"No completed price bars for {normalized}."}

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


def _parse_yf_news_item(item: dict) -> dict | None:
    """Normalise one yfinance news entry across both payload shapes.

    yfinance < 0.2.55 returned a flat dict (title/publisher/link/
    providerPublishTime). Newer versions nest everything under ``content`` with
    renamed keys, which silently produced all-null articles.
    """
    if not isinstance(item, dict):
        return None

    content = item.get("content")
    if isinstance(content, dict):
        provider = content.get("provider") or {}
        url = (
            (content.get("canonicalUrl") or {}).get("url")
            or (content.get("clickThroughUrl") or {}).get("url")
        )
        title = content.get("title")
        publisher = provider.get("displayName")
        publish_time = content.get("pubDate") or content.get("displayTime")
    else:
        title = item.get("title")
        publisher = item.get("publisher")
        publish_time = item.get("providerPublishTime")
        url = item.get("link")

    if not title:
        return None

    return {
        "title": title,
        "publisher": publisher,
        "publish_time": publish_time,
        "link": url,
        "source": "yfinance",
    }


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

        for item in yf_news or []:
            if len(articles) >= max_articles:
                break
            parsed = _parse_yf_news_item(item)
            if parsed is not None:
                articles.append(parsed)
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
    position_value_idr: float,
    buy_fee_rate: float = 0.0015,
    sell_fee_rate: float = 0.0025,
) -> dict:
    """Calculate deterministic Expected Value accounting for IDX friction.

    IDX brokerage fees are charged on *transaction value*, not on the profit or
    loss of the trade, so both legs are priced off ``position_value_idr``:

        fees_total = position_value_idr * (buy_fee_rate + sell_fee_rate)
        net_win    = profit_target_idr - fees_total
        net_loss   = loss_target_idr   + fees_total
        ev         = p * net_win - (1 - p) * net_loss

    ``profit_target_idr`` and ``loss_target_idr`` are gross, positive IDR
    magnitudes (the loss is passed as a positive number).
    """
    try:
        if not (0.0 <= win_prob <= 1.0):
            return {"error": True, "message": "win_prob must be between 0.0 and 1.0"}
        if position_value_idr is None or position_value_idr <= 0:
            return {
                "error": True,
                "message": (
                    "position_value_idr must be a positive IDR amount — it is the "
                    "transaction value that IDX brokerage fees are charged on."
                ),
            }
        if profit_target_idr < 0 or loss_target_idr < 0:
            return {
                "error": True,
                "message": (
                    "profit_target_idr and loss_target_idr must both be positive "
                    "magnitudes (pass the loss as a positive number)."
                ),
            }
        if buy_fee_rate < 0 or sell_fee_rate < 0:
            return {"error": True, "message": "Fee rates must be non-negative."}

        buy_fee_idr = position_value_idr * buy_fee_rate
        sell_fee_idr = position_value_idr * sell_fee_rate
        fees_total_idr = buy_fee_idr + sell_fee_idr

        net_win = profit_target_idr - fees_total_idr
        net_loss = loss_target_idr + fees_total_idr

        ev = (win_prob * net_win) - ((1.0 - win_prob) * net_loss)

        # Breakeven win probability: the p at which EV == 0.
        denom = net_win + net_loss
        breakeven_win_prob = (net_loss / denom) if denom > 0 else None

        return {
            "ev_idr": ev,
            "ev_pct_of_position": (ev / position_value_idr) * 100.0,
            "net_win_idr": net_win,
            "net_loss_idr": net_loss,
            "position_value_idr": position_value_idr,
            "buy_fee_idr": buy_fee_idr,
            "sell_fee_idr": sell_fee_idr,
            "fees_total_idr": fees_total_idr,
            "win_prob": win_prob,
            "breakeven_win_prob": breakeven_win_prob,
            "edge_vs_breakeven": (
                win_prob - breakeven_win_prob if breakeven_win_prob is not None else None
            ),
            "buy_fee_rate": buy_fee_rate,
            "sell_fee_rate": sell_fee_rate,
            "fee_basis": "transaction value (position_value_idr), both legs",
        }
    except Exception as e:
        logger.exception("Failed to calculate EV")
        return {"error": True, "message": f"Failed to calculate EV: {str(e)}"}


async def log_prediction_snapshot(
    ticker: str,
    initial_ev: float,
    ai_win_prob: float,
    reasoning: str,
    target_date: str,
    strategy_name: str,
    position_value_idr: float | None = None,
    profit_target_idr: float | None = None,
    loss_target_idr: float | None = None,
) -> dict:
    """Append the AI agent's trade thesis into a structured predictions_log.json file."""
    try:
        normalized = validate_ticker(ticker)
    except ValueError as e:
        return {"error": True, "message": str(e)}

    try:
        datetime.strptime(target_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return {
            "error": True,
            "message": f"target_date must be YYYY-MM-DD, got {target_date!r}.",
        }

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": normalized,
        "strategy": strategy_name,
        "initial_ev": initial_ev,
        "ai_win_prob": ai_win_prob,
        "position_value_idr": position_value_idr,
        "profit_target_idr": profit_target_idr,
        "loss_target_idr": loss_target_idr,
        "reasoning": reasoning,
        "target_date": target_date,
        "schema_version": 2,
    }

    try:
        with _log_lock:
            log_file = predictions_log_file()
            logs = _read_predictions_log(log_file)
            logs.append(snapshot)
            _atomic_write_json(log_file, logs)

        return {
            "success": True,
            "message": f"Prediction snapshot for {normalized} successfully logged.",
            "timestamp": snapshot["timestamp"],
            "log_file": str(log_file),
            "total_predictions_logged": len(logs),
        }
    except Exception as e:
        logger.exception("Failed to write prediction snapshot.")
        return {"error": True, "message": f"Failed to log prediction: {str(e)}"}


def _read_predictions_log(log_file: Path) -> list[dict]:
    """Load the predictions log, quarantining a corrupt file instead of failing forever.

    A truncated or hand-edited log used to make every subsequent write fail. The
    bad file is moved aside once so logging can continue.
    """
    if not log_file.exists():
        return []

    content = log_file.read_text(encoding="utf-8").strip()
    if not content:
        return []

    try:
        logs = json.loads(content)
    except json.JSONDecodeError:
        corrupt = log_file.with_suffix(
            f".corrupt-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        log_file.replace(corrupt)
        logger.error("predictions log was not valid JSON; moved to %s", corrupt)
        return []

    if not isinstance(logs, list):
        logger.error("predictions log root was %s, expected list; starting fresh", type(logs).__name__)
        return []
    return logs


def _atomic_write_json(path: Path, payload: list[dict]) -> None:
    """Write via a temp file + replace so a crash mid-write cannot truncate the log."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


_GATHER_TIMEOUT = 45.0


async def gather_intelligence(ticker: str, lookback_days: int = 60, max_articles: int = 5) -> dict:
    """Fetch trade setup and recent news in a single call.

    The two legs are independent: a news outage must not take the price setup
    down with it, so failures are captured per leg rather than propagated.
    """
    try:
        lookback_days = max(1, int(lookback_days))
        max_articles = max(0, int(max_articles))
    except (TypeError, ValueError):
        return {"error": True, "message": "lookback_days and max_articles must be integers."}

    async def _guard(coro, label):
        try:
            return await asyncio.wait_for(coro, timeout=_GATHER_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("%s timed out after %.0fs for %s", label, _GATHER_TIMEOUT, ticker)
            return {"error": True, "message": f"{label} timed out after {_GATHER_TIMEOUT:.0f}s."}
        except Exception as e:
            logger.exception("%s failed for %s", label, ticker)
            return {"error": True, "message": f"{label} failed: {e}"}

    setup_result, news_result = await asyncio.gather(
        _guard(get_trade_setup(ticker, lookback_days), "trade_setup"),
        _guard(fetch_idx_news(ticker, max_articles), "news_catalysts"),
    )

    return {
        "trade_setup": setup_result,
        "news_catalysts": news_result,
        "workflow_instruction": (
            "Evaluate the setup and news to determine win_prob, then call "
            "evaluate_and_log_thesis with the intended position_value_idr."
        ),
    }


async def evaluate_and_log_thesis(
    ticker: str,
    win_prob: float,
    profit_target_idr: float,
    loss_target_idr: float,
    position_value_idr: float,
    reasoning: str,
    target_date: str,
    strategy_name: str,
    buy_fee_rate: float = 0.0015,
    sell_fee_rate: float = 0.0025,
) -> dict:
    """Calculate EV for a thesis and log the snapshot for forward testing.

    The snapshot is logged for every valid thesis, including negative-EV ones —
    forward testing needs the rejected trades too. ``ev_verdict`` reports whether
    the edge is positive.
    """
    ev_result = await calculate_expected_value(
        win_prob,
        profit_target_idr,
        loss_target_idr,
        position_value_idr,
        buy_fee_rate,
        sell_fee_rate,
    )

    if ev_result.get("error"):
        return ev_result

    log_result = await log_prediction_snapshot(
        ticker,
        ev_result["ev_idr"],
        win_prob,
        reasoning,
        target_date,
        strategy_name,
        position_value_idr=position_value_idr,
        profit_target_idr=profit_target_idr,
        loss_target_idr=loss_target_idr,
    )

    return {
        "expected_value_analysis": ev_result,
        "ev_verdict": "positive_edge" if ev_result["ev_idr"] > 0 else "negative_edge",
        "logging_status": log_result,
    }
