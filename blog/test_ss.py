#!/usr/bin/env python3
# test_ss.py - Test script for stockscore.py /stockta endpoint

import requests
import json
import sys
import time

sys.path.insert(0, "/home/flask")
import config

BASE_URL = config.stockscore_url.rstrip('/')
RESOURCE_ID = 0


def test_stockta(symbol, resource_id=RESOURCE_ID):
    """Test the /stockta endpoint"""
    
    url = f"{BASE_URL}/stockta/{resource_id}/{symbol}"
    print(f"\n{'='*60}")
    print(f"Testing: {url}")
    print('='*60)
    
    try:
        start = time.time()
        resp = requests.get(url, timeout=10)
        elapsed = time.time() - start
        
        print(f"Status: {resp.status_code}")
        print(f"Time: {elapsed*1000:.0f}ms")
        print(f"Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
        
        # Debug: show raw response if short
        if len(resp.text) < 500:
            print(f"Raw response: {resp.text[:500]}")
        
        if resp.status_code != 200:
            print(f"Error: {resp.text}")
            return False
        
        if not resp.text:
            print("❌ Empty response")
            return False
        
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            print(f"❌ JSON decode error: {e}")
            print(f"Response text: {resp.text[:500]}")
            return False
        
        # Validate structure
        required_keys = ['ticker', 'price', 'moving_averages', 'week_52', 
                        'momentum', 'volatility', 'price_changes', 'volume', 
                        'signals', 'scores']
        
        missing = [k for k in required_keys if k not in data]
        if missing:
            print(f"❌ Missing keys: {missing}")
            return False
        
        print(f"✅ All required keys present")
        
        # Print summary
        print(f"\n📊 {data['ticker']} - {data.get('date', 'N/A')}")
        print(f"   Price: ${data['price']['current']}")
        
        # Moving averages
        print(f"\n   Moving Averages:")
        for ma_name, ma_data in data['moving_averages'].items():
            if ma_data:
                arrow = '↑' if ma_data['above'] else '↓'
                print(f"      {ma_name.upper():8} ${ma_data['value']:>8.2f} {arrow} {ma_data['pct_diff']:+.1f}%")
            else:
                print(f"      {ma_name.upper():8} N/A")
        
        # 52-week range
        w52 = data['week_52']
        print(f"\n   52-Week Range:")
        print(f"      High: ${w52['high']:.2f} ({w52['pct_from_high']:+.1f}%)")
        print(f"      Low:  ${w52['low']:.2f} ({w52['pct_from_low']:+.1f}%)")
        print(f"      Position: {w52['range_position_pct']:.1f}%")
        
        # Momentum
        mom = data['momentum']
        print(f"\n   Momentum:")
        print(f"      RSI(14): {mom['rsi_14']}")
        if mom['macd']:
            print(f"      MACD: {mom['macd']['macd']:.3f} / Signal: {mom['macd']['signal']}")
        print(f"      ADX(14): {mom['adx_14']}")
        
        # Volatility
        vol = data['volatility']
        print(f"\n   Volatility:")
        print(f"      ATR(14): ${vol['atr_14']} ({vol['atr_pct']:.1f}%)")
        if vol['bollinger_bands']:
            bb = vol['bollinger_bands']
            print(f"      BB: ${bb['lower']:.2f} - ${bb['middle']:.2f} - ${bb['upper']:.2f}")
        
        # Price changes
        print(f"\n   Price Changes:")
        pc = data['price_changes']
        for period in ['1d', '5d', '1m', '3m', '6m', '1y']:
            val = pc.get(period)
            if val is not None:
                emoji = '🟢' if val > 0 else '🔴' if val < 0 else '⚪'
                print(f"      {period:4} {emoji} {val:+.1f}%")
        
        # Volume
        v = data['volume']
        print(f"\n   Volume:")
        print(f"      Avg 30d: {v['avg_30d']:,}")
        print(f"      Today:   {v['today']:,}")
        print(f"      RVOL:    {v['rvol']:.2f}x")
        
        # Signals
        print(f"\n   Signals: {', '.join(data['signals'])}")
        
        # Scores
        print(f"\n   Scores: Long={data['scores']['long']} Short={data['scores']['short']}")
        
        return True
        
    except requests.exceptions.ConnectionError:
        print(f"❌ Connection failed - is stockscore.py running at {BASE_URL}?")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_cache(symbol, resource_id=RESOURCE_ID):
    """Test that caching is working by making two requests"""
    
    url = f"{BASE_URL}/stockta/{resource_id}/{symbol}"
    print(f"\n{'='*60}")
    print(f"Cache Test: {symbol}")
    print('='*60)
    
    try:
        # First request (should hit DB/calculate)
        start1 = time.time()
        resp1 = requests.get(url, timeout=10)
        time1 = (time.time() - start1) * 1000
        
        # Second request (should hit cache)
        start2 = time.time()
        resp2 = requests.get(url, timeout=10)
        time2 = (time.time() - start2) * 1000
        
        print(f"   Request 1: {time1:.0f}ms (status: {resp1.status_code})")
        print(f"   Request 2: {time2:.0f}ms (status: {resp2.status_code})")
        
        if resp1.status_code != 200 or resp2.status_code != 200:
            print(f"   ❌ Non-200 status code")
            return False
        
        if time2 < time1 * 0.5:
            print(f"   ✅ Cache appears to be working (2nd request {time1/time2:.1f}x faster)")
        else:
            print(f"   ⚠️  Cache may not be working (times similar)")
        
        # Verify same data
        try:
            data1 = resp1.json()
            data2 = resp2.json()
            if data1 == data2:
                print(f"   ✅ Response data identical")
            else:
                print(f"   ❌ Response data differs!")
        except json.JSONDecodeError as e:
            print(f"   ❌ JSON decode error: {e}")
            return False
            
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_original_endpoint(symbol, resource_id=RESOURCE_ID):
    """Test that original /stockscore endpoint still works"""
    
    url = f"{BASE_URL}/stockscore/{resource_id}/{symbol}"
    print(f"\n{'='*60}")
    print(f"Original Endpoint Test: {url}")
    print('='*60)
    
    try:
        resp = requests.get(url, timeout=10)
        print(f"Status: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
        
        # Debug: show raw response if short
        if len(resp.text) < 500:
            print(f"Raw response: {resp.text[:500]}")
        
        if not resp.text:
            print("❌ Empty response")
            return False
        
        if resp.status_code == 200:
            try:
                data = resp.json()
            except json.JSONDecodeError as e:
                print(f"❌ JSON decode error: {e}")
                return False
            print(f"✅ Original endpoint working")
            print(f"   lscore: {data.get('lscore')}")
            print(f"   sscore: {data.get('sscore')}")
            print(f"   lscore1: {data.get('lscore1')}")
            print(f"   sscore1: {data.get('sscore1')}")
            return True
        else:
            print(f"❌ Error: {resp.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


if __name__ == "__main__":
    # Smoke test
    symbol = 'AAPL'
    
    print(f"Testing stockscore service at {BASE_URL}")
    print(f"Symbol: {symbol}, ResourceID: {RESOURCE_ID}")
    
    # Test home
    resp = requests.get(f"{BASE_URL}/", timeout=5)
    print(f"\nHome: {resp.json()}")
    
    # Test stockta
    test_stockta(symbol)
    
    # Test original endpoint
    test_original_endpoint(symbol)
    
    # Test cache
    test_cache(symbol)