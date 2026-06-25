"""
get_price_EOD.py
================
Retrieve current stock price from EODHD API.

Usage:
    from get_price_EOD import get_current_price
    price = get_current_price("MSFT", "US")
"""

import sys
sys.path.insert(0, '/home/flask')
import config

import requests
from typing import Optional, Dict, Any


def get_current_price(symbol: str, exchange: str = "US") -> Optional[float]:
    """
    Get the current/latest stock price from EODHD.
    
    Args:
        symbol: Stock ticker (e.g., "MSFT", "AAPL")
        exchange: Exchange code (e.g., "US", "LSE", "TO")
    
    Returns:
        Current price as float, or None if error
    """
    # EODHD real-time endpoint
    url = f"https://eodhd.com/api/real-time/{symbol}.{exchange}"
    
    params = {
        "api_token": config.EOD_token,
        "fmt": "json"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # The 'close' field contains the latest price — may be 'NA' when market is closed
        return _safe_float(data.get("close"))

    except requests.RequestException as e:
        print(f"[EODHD] Request error: {e}")
        return None
    except (ValueError, KeyError) as e:
        print(f"[EODHD] Parse error: {e}")
        return None


def _safe_float(val) -> Optional[float]:
    """Convert a value to float, returning None for 'NA', None, or unparseable values."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _try_realtime_service(symbol: str, exchange: str) -> Optional[Dict[str, Any]]:
    """Try to get quote from the realtime price service (fast, no EODHD token cost)."""
    rt_url = getattr(config, 'realtime_service_url', None)
    if not rt_url:
        return None
    # Realtime service caches US equities by symbol name only.
    # COMM (commodities/futures) symbols can collide with stock tickers
    # (e.g. ES = Eversource Energy vs E-mini S&P 500 futures).
    # Always go direct to EODHD for COMM so the exchange qualifier is used.
    if exchange == "COMM":
        return None
    try:
        resp = requests.get(f"{rt_url.rstrip('/')}/prices/{symbol}", timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if 'error' in data:
            return None
        return {
            "symbol": symbol,
            "exchange": exchange,
            "open":          _safe_float(data.get("open")),
            "high":          _safe_float(data.get("high")),
            "low":           _safe_float(data.get("low")),
            "close":         _safe_float(data.get("price")),
            "volume":        _safe_float(data.get("volume")),
            "previousClose": _safe_float(data.get("previous_close")),
            "change":        _safe_float(data.get("change")),
            "change_p":      _safe_float(data.get("change_p")),
            "timestamp":     data.get("timestamp"),
        }
    except Exception:
        return None


def _eodhd_direct(symbol: str, exchange: str = "US") -> Optional[Dict[str, Any]]:
    """
    Authoritative quote straight from EODHD, bypassing the realtime cache service
    (which is stale outside regular hours). Uses the real-time endpoint; if its
    'close' is unavailable ('NA'), falls back to the most recent official EOD close.
    Always sets 'source'; sets 'as_of_date' from the EOD fallback (the real-time
    endpoint carries a unix 'timestamp' instead).
    """
    p = {"api_token": config.EOD_token, "fmt": "json"}
    # 1) real-time close — returns the last close even when the market is shut
    try:
        d = requests.get(f"https://eodhd.com/api/real-time/{symbol}.{exchange}",
                         params=p, timeout=10).json()
        if _safe_float(d.get("close")) is not None:
            return {
                "symbol": symbol, "exchange": exchange,
                "open":  _safe_float(d.get("open")),  "high": _safe_float(d.get("high")),
                "low":   _safe_float(d.get("low")),   "close": _safe_float(d.get("close")),
                "volume": _safe_float(d.get("volume")),
                "previousClose": _safe_float(d.get("previousClose")),
                "change": _safe_float(d.get("change")), "change_p": _safe_float(d.get("change_p")),
                "timestamp": d.get("timestamp"), "as_of_date": None, "source": "eodhd-realtime",
            }
    except Exception as e:
        print(f"[EODHD] real-time error {symbol}.{exchange}: {e}")
    # 2) fall back to the last official EOD close (carries an explicit date)
    try:
        rows = requests.get(f"https://eodhd.com/api/eod/{symbol}.{exchange}",
                            params={**p, "order": "d"}, timeout=10).json()
        last = rows[0] if isinstance(rows, list) and rows else {}
        if _safe_float(last.get("close")) is not None:
            return {
                "symbol": symbol, "exchange": exchange,
                "open":  _safe_float(last.get("open")),  "high": _safe_float(last.get("high")),
                "low":   _safe_float(last.get("low")),   "close": _safe_float(last.get("close")),
                "volume": _safe_float(last.get("volume")),
                "previousClose": None, "change": None, "change_p": None,
                "timestamp": None, "as_of_date": last.get("date"), "source": "eodhd-eod",
            }
    except Exception as e:
        print(f"[EODHD] eod fallback error {symbol}.{exchange}: {e}")
    return None


def get_quote_details(symbol: str, exchange: str = "US",
                      use_realtime: bool = True) -> Optional[Dict[str, Any]]:
    """
    Get full quote details.

    use_realtime=True  (default): try the realtime cache service first (free/fast —
        appropriate for the live ticker bar), falling back to direct EODHD.
    use_realtime=False: go DIRECT to EODHD — the authoritative source. Use this for
        ARTICLE CREATION, where the printed price must be correct and the realtime
        cache is stale outside regular hours.

    Returns numeric fields as float or None (never 'NA'), plus a 'source' tag
    ('realtime-service' | 'eodhd-realtime' | 'eodhd-eod') and an 'as_of_date'
    (set when the EOD close is used; the real-time path carries a unix 'timestamp').
    """
    if use_realtime:
        result = _try_realtime_service(symbol, exchange)
        if result and result.get("close") is not None:
            result.setdefault("as_of_date", None)
            result["source"] = "realtime-service"
            return result
    return _eodhd_direct(symbol, exchange)


# ============================================================
# SMOKE TEST
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("EODHD Price Retrieval - Smoke Test")
    print("=" * 50)
    
    # Test 1: Get simple price for MSFT
    print("\n[Test 1] Get current price for MSFT (US)...")
    price = get_current_price("MSFT", "US")
    if price:
        print(f"  ✓ MSFT current price: ${price:.2f}")
    else:
        print(f"  ✗ Failed to get price")
    
    # Test 2: Get full quote details for MSFT
    print("\n[Test 2] Get full quote details for MSFT (US)...")
    quote = get_quote_details("MSFT", "US")
    if quote:
        print(f"  ✓ Quote details:")
        print(f"      Symbol:    {quote['symbol']}.{quote['exchange']}")
        print(f"      Open:      ${quote['open']}")
        print(f"      High:      ${quote['high']}")
        print(f"      Low:       ${quote['low']}")
        print(f"      Close:     ${quote['close']}")
        print(f"      Volume:    {quote['volume']:,}" if quote['volume'] else "      Volume:    N/A")
        print(f"      Prev Close: ${quote['previousClose']}")
        print(f"      Change:    {quote['change']} ({quote['change_p']}%)")
    else:
        print(f"  ✗ Failed to get quote details")
    
    # Test 3: Test with another symbol
    print("\n[Test 3] Get current price for AAPL (US)...")
    price_aapl = get_current_price("AAPL", "US")
    if price_aapl:
        print(f"  ✓ AAPL current price: ${price_aapl:.2f}")
    else:
        print(f"  ✗ Failed to get price")
    
    print("\n" + "=" * 50)
    print("Smoke test complete!")
    print("=" * 50)