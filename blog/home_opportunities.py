# the main purpose of this script is to generate a list of opportunities for the home page of tradewave.  should run at least weekly

# This script gets all opportunities for a week from appserver (Saturday through Friday) for S&P 500 stocks
# It combines results from multiple day_ranges and modes (consecutive and PE)
# Run every Friday night to refresh the list for the following week

import datetime
from datetime import timedelta
import requests
import sys
import csv
import os
import shutil
import time

sys.path.insert(0, '/home/flask')
import config

from blog_tools import get_company_name, convert_param_base64

# from thumbnail_tools import get_chart_historical_prices, get_cumulative_chart_data
# from thumbnail_tools import build_stats_triplet, build_trend_segment, inc_date_day

# ==================== CONFIGURATION ====================
# Day ranges to query
DAY_RANGES = ["7-30", "31-60"]

# Output CSV file
CSV_FILE = "home_opportunities.csv"

# Archive folder for old CSV files
ARCHIVE_FOLDER = "home_opp_archive" 

# Week boundaries (lowercase day names)
START_DAY = "saturday"
END_DAY = "friday"

# Resource ID (2 = S&P 500)
RESOURCE_ID = 2

# Years configuration (used for both cons and pe modes)
YEARS = 10

# pyears configuration
STARTING_PYEARS = 10
MIN_PYEARS = 8

# Minimum unique opportunities required before reducing pyears
MIN_UNIQUE_OPPS = 10

# Number of top opportunities to keep per mode (consecutive and PE)
TOP_OPPS_PER_MODE = 10

# Minimum average profit percentage to include
MIN_AVGP = 5

# Rank/sort by: 'TWR' or 'SR' (used when selecting top opportunities)
RANK_BY = 'SR'

# Server URLs
prod_appserver_url = 'https://app1pp.trxstat.com'
# prod_appserver_url = 'http://192.168.1.151:5000'
stage_appserver_url = 'https://app1stage.trxstat.com'

userid = 16  # afshin's userid

# Minimum stock price filter (exclude stocks below this price)
MIN_STOCK_PRICE = 60.0

# Stockscore service URL for price checks
STOCKSCORE_URL = config.stockscore_url.rstrip('/')
STOCKSCORE_RESOURCE_ID = 0  # Resource ID for stockscore API
# ===========================================================


# Day name to weekday number mapping (Monday = 0, Sunday = 6)
DAY_NAME_TO_NUM = {
    'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
    'friday': 4, 'saturday': 5, 'sunday': 6
}


def get_next_weekday(day_name, from_date=None):
    """Get the next occurrence of a weekday from a given date."""
    if from_date is None:
        from_date = datetime.date.today()
    target = DAY_NAME_TO_NUM[day_name.lower()]
    days_ahead = target - from_date.weekday()
    if days_ahead <= 0:  # Target day already happened this week or is today
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)


def get_stock_price(symbol):
    """
    Get current stock price from stockscore API.
    Returns price as float, or None if unable to fetch.
    """
    url = f"{STOCKSCORE_URL}/stockta/{STOCKSCORE_RESOURCE_ID}/{symbol}"
    
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if 'price' in data and 'current' in data['price']:
                price = float(data['price']['current'])
                return price
    except Exception as e:
        print(f"    Warning: Failed to get price for {symbol}: {e}")
    
    return None


def get_dates_for_week(start_date, end_date):
    """
    Get the list of dates to call the API for.
    Saturday call covers Sat, Sun, Mon.
    Then we need separate calls for Tue, Wed, Thu, Fri.
    """
    dates_to_call = []
    current = start_date
    
    while current <= end_date:
        weekday = current.weekday()
        
        # Saturday (5), Sunday (6), Monday (0) are covered by Saturday call
        if weekday == 5:  # Saturday
            dates_to_call.append(current)
            current += timedelta(days=3)  # Skip to Tuesday
        elif weekday == 6:  # Sunday - should be covered by Saturday, but just in case
            # Find the Saturday before
            sat = current - timedelta(days=1)
            if sat not in dates_to_call and sat >= start_date:
                dates_to_call.append(sat)
            current += timedelta(days=2)  # Skip to Tuesday
        elif weekday == 0:  # Monday - should be covered by Saturday
            # Find the Saturday before
            sat = current - timedelta(days=2)
            if sat not in dates_to_call and sat >= start_date:
                dates_to_call.append(sat)
            current += timedelta(days=1)  # Move to Tuesday
        else:
            # Tuesday through Friday - each needs its own call
            dates_to_call.append(current)
            current += timedelta(days=1)
    
    return sorted(set(dates_to_call))


def get_keyprovider_token():
    url = prod_appserver_url + '/login/16/7/4/5/6'
    api_result = requests.get(url)
    result = api_result.json()
    t = result['message'].split(' ')
    return t[4]


def login_appserver(keyprovider_token):
    """After logging in, the returned token is used to make other calls to the appserver."""
    url = prod_appserver_url + '/login/16/7/4/5/' + keyprovider_token
    
    if '5000' in prod_appserver_url:
        url = prod_appserver_url + '/login/16/9/4/5/' + keyprovider_token
    
    api_result = requests.get(url)
    print('login api_result=', api_result)
    result = api_result.json()

    if 'message' in result:  # login failed due to timing - try again
        time.sleep(10)
        api_result = requests.get(url)
        result = api_result.json()
        if 'message' in result:
            print('message:', result['message'])
            return -1
        else:
            print('attempt 2 to login succeeded')

    return result['token']


def get_opp_list(group_id, month, day, years, pyears, day_range, token, mode='cons'):
    """Get opportunities list from TradeWave API.
    
    Args:
        group_id: Resource ID (e.g., 2 for S&P 500)
        month: Month name (e.g., 'March')
        day: Day of month (e.g., '2')
        years: Years string (e.g., '10')
        pyears: Number of profitable years required
        day_range: Day range string (e.g., '7-30')
        token: API token
        mode: 'cons' or 'pe'
    """
    url = f'{prod_appserver_url}/OppList4/{group_id}/{month}/{day}/{years}/{pyears}/{day_range}/0/0?token={token}&mode={mode}'
    api_result = requests.get(url)
    result = api_result.json()
    return result


def fetch_opportunities_for_combination(dates_to_call, day_range, mode, years_str, token, start_date, end_date):
    """
    Fetch opportunities for a specific (day_range, mode) combination.
    Tries with decreasing pyears until MIN_UNIQUE_OPPS is reached or MIN_PYEARS is hit.
    
    Returns list of opportunities (as dicts) that fall within the date range.
    """
    all_opps = []
    
    for pyears in range(STARTING_PYEARS, MIN_PYEARS - 1, -1):
        all_opps = []
        
        for call_date in dates_to_call:
            month_name = call_date.strftime('%B')
            day_num = str(call_date.day)
            
            try:
                result = get_opp_list(
                    RESOURCE_ID, month_name, day_num, 
                    years_str, str(pyears), day_range, 
                    token, mode
                )
                
                if 'OppList' in result:
                    for opp in result['OppList']:
                        # opp format: [start_date, symbol, days, direction, SR, AvgP, median, TWA, TWR]
                        opp_date_str = opp[0]
                        opp_date = datetime.datetime.strptime(opp_date_str, '%Y-%m-%d').date()
                        
                        # Filter to only include opportunities within our date range
                        if start_date <= opp_date <= end_date:
                            all_opps.append({
                                'start_date': opp[0],
                                'symbol': opp[1],
                                'days': opp[2],
                                'direction': opp[3],
                                'SR': opp[4],
                                'AvgP': opp[5],
                                'median': opp[6],
                                'TWA': opp[7],
                                'TWR': opp[8],
                                'day_range': day_range,
                                'mode': mode
                            })
            except Exception as e:
                print(f"Error fetching {mode} {day_range} for {call_date}: {e}")
                continue
        
        # Count unique symbols
        unique_symbols = set(opp['symbol'] for opp in all_opps)
        print(f"  {mode} {day_range} with pyears={pyears}: {len(unique_symbols)} unique symbols, {len(all_opps)} total opps")
        
        if len(unique_symbols) >= MIN_UNIQUE_OPPS:
            break
        else:
            print(f"    Less than {MIN_UNIQUE_OPPS} unique, trying pyears={pyears-1}...")
    
    return all_opps


def dedupe_by_symbol_keep_highest(opps_list, rank_by='TWR'):
    """
    Given a list of opportunity dicts, keep only one entry per symbol.
    For duplicates, keep the one with the highest value of rank_by field.
    """
    symbol_best = {}
    
    for opp in opps_list:
        symbol = opp['symbol']
        value = float(opp[rank_by])
        
        if symbol not in symbol_best or value > float(symbol_best[symbol][rank_by]):
            symbol_best[symbol] = opp
    
    return list(symbol_best.values())


def archive_old_csv(csv_path, archive_folder):
    """Archive the existing CSV file with today's date."""
    if os.path.exists(csv_path):
        # Create archive folder if it doesn't exist
        if not os.path.exists(archive_folder):
            os.makedirs(archive_folder)
            print(f"Created archive folder: {archive_folder}")
        
        # Generate archive filename with date
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        base_name = os.path.splitext(os.path.basename(csv_path))[0]
        archive_path = os.path.join(archive_folder, f"{base_name}_{today_str}.csv")
        
        # Handle case where archive already exists today (add counter)
        counter = 1
        original_archive_path = archive_path
        while os.path.exists(archive_path):
            archive_path = os.path.join(archive_folder, f"{base_name}_{today_str}_{counter}.csv")
            counter += 1
        
        shutil.copy2(csv_path, archive_path)
        print(f"Archived old CSV to: {archive_path}")


def enrich_opportunities(opps_list, resource_id, years):
    """Add company_name and pattern_param to each opportunity."""
    for opp in opps_list:
        # Get company name
        try:
            company = get_company_name(resource_id, opp['symbol']) or opp['symbol']
        except Exception:
            company = opp['symbol']
        opp['company_name'] = company
        
        # Determine years string based on mode
        if opp['mode'] == 'pe':
            # Extract year from start_date and calculate PE cycle year (0-3)
            start_date_str = str(opp['start_date'])
            pattern_year = int(start_date_str.split('-')[0])
            pe_cycle_year = pattern_year % 4
            years_str = f"PE{pe_cycle_year}-{years}"
        else:
            # Consecutive mode uses plain years value
            years_str = str(years)
        
        # Generate pattern_param
        try:
            param = convert_param_base64(
                str(resource_id), 
                str(opp['symbol']), 
                str(opp['start_date']), 
                str(opp['days']), 
                years_str
            )
            opp['pattern_param'] = param if param else ''
            print(f"  {opp['symbol']}: mode={opp['mode']}, years_str={years_str}, pattern_param = {param}")
        except Exception as e:
            print(f"  ERROR generating pattern_param for {opp['symbol']}: {e}")
            opp['pattern_param'] = ''
    
    return opps_list


def save_to_csv(opps_list, csv_path):
    """Save opportunities to CSV file."""
    fieldnames = ['start_date', 'symbol', 'company_name', 'days', 'direction', 'SR', 'AvgP', 'median', 'TWA', 'TWR', 'day_range', 'mode', 'pattern_param']
    
    with open(csv_path, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(opps_list)
    
    print(f"Saved {len(opps_list)} opportunities to {csv_path}")


if __name__ == '__main__':
    print("=" * 60)
    print("Home Opportunities Generator")
    print("=" * 60)
    
    # Login
    print("\nLogging in...")
    keyprovider_token = get_keyprovider_token()
    appserver_token = login_appserver(keyprovider_token)
    
    if appserver_token == -1:
        print("Failed to login. Exiting.")
        sys.exit(1)
    
    # Calculate week boundaries
    today = datetime.date.today()
    start_date = get_next_weekday(START_DAY, today)
    
    # Calculate end date (next occurrence of END_DAY after start_date)
    end_date = start_date
    target_end = DAY_NAME_TO_NUM[END_DAY.lower()]
    while end_date.weekday() != target_end or end_date == start_date:
        end_date += timedelta(days=1)
    
    print(f"\nWeek range: {start_date} ({start_date.strftime('%A')}) to {end_date} ({end_date.strftime('%A')})")
    
    # Get dates to call API for
    dates_to_call = get_dates_for_week(start_date, end_date)
    print(f"API call dates: {[str(d) for d in dates_to_call]}")
    
    # Years string (same for both modes)
    years_str = str(YEARS)
    
    print(f"\nYears: '{years_str}' (modes: cons and pe)")
    print(f"Ranking by: {RANK_BY}")
    
    # Fetch opportunities for all combinations
    consecutive_opportunities = []
    pe_opportunities = []
    
    for day_range in DAY_RANGES:
        print(f"\n--- Processing day_range: {day_range} ---")
        
        # Consecutive mode
        print(f"\nFetching cons mode...")
        cons_opps = fetch_opportunities_for_combination(
            dates_to_call, day_range, 'cons', 
            years_str, appserver_token, 
            start_date, end_date
        )
        consecutive_opportunities.extend(cons_opps)
        
        # PE mode
        print(f"\nFetching pe mode...")
        pe_opps = fetch_opportunities_for_combination(
            dates_to_call, day_range, 'pe', 
            years_str, appserver_token, 
            start_date, end_date
        )
        pe_opportunities.extend(pe_opps)
    
    print(f"\n{'=' * 60}")
    print(f"Consecutive opportunities (raw): {len(consecutive_opportunities)}")
    print(f"PE opportunities (raw): {len(pe_opportunities)}")
    
    # Deduplicate each mode separately, keeping highest by RANK_BY
    consecutive_deduped = dedupe_by_symbol_keep_highest(consecutive_opportunities, RANK_BY)
    pe_deduped = dedupe_by_symbol_keep_highest(pe_opportunities, RANK_BY)
    
    print(f"Consecutive unique symbols: {len(consecutive_deduped)}")
    print(f"PE unique symbols: {len(pe_deduped)}")
    
    # Filter by minimum AvgP
    consecutive_deduped = [opp for opp in consecutive_deduped if float(opp['AvgP']) >= MIN_AVGP]
    pe_deduped = [opp for opp in pe_deduped if float(opp['AvgP']) >= MIN_AVGP]
    
    print(f"Consecutive after AvgP >= {MIN_AVGP}% filter: {len(consecutive_deduped)}")
    print(f"PE after AvgP >= {MIN_AVGP}% filter: {len(pe_deduped)}")
    
    # Sort each by RANK_BY descending
    consecutive_deduped.sort(key=lambda x: float(x[RANK_BY]), reverse=True)
    pe_deduped.sort(key=lambda x: float(x[RANK_BY]), reverse=True)
    
    # Filter by minimum stock price and take top N for each mode
    print(f"\nFiltering stocks with price >= ${MIN_STOCK_PRICE}...")
    
    def select_top_with_price_filter(opps_list, num_needed, mode_name):
        """Select top N opportunities that meet price criteria."""
        selected = []
        for opp in opps_list:
            if len(selected) >= num_needed:
                break
            
            symbol = opp['symbol']
            price = get_stock_price(symbol)
            
            if price is None:
                print(f"  {mode_name}: {symbol} - Unable to fetch price, skipping")
                continue
            
            if price < MIN_STOCK_PRICE:
                print(f"  {mode_name}: {symbol} - Price ${price:.2f} below minimum, skipping")
                continue
            
            print(f"  {mode_name}: {symbol} - Price ${price:.2f} ✓")
            selected.append(opp)
        
        return selected
    
    top_consecutive = select_top_with_price_filter(consecutive_deduped, TOP_OPPS_PER_MODE, "CONS")
    top_pe = select_top_with_price_filter(pe_deduped, TOP_OPPS_PER_MODE, "PE")
    
    print(f"\nTop {TOP_OPPS_PER_MODE} consecutive (by {RANK_BY}, price >= ${MIN_STOCK_PRICE}): {len(top_consecutive)}")
    print(f"Top {TOP_OPPS_PER_MODE} PE (by {RANK_BY}, price >= ${MIN_STOCK_PRICE}): {len(top_pe)}")
    
    # Combine and sort by date
    final_opps = top_consecutive + top_pe
    final_opps.sort(key=lambda x: x['start_date'])
    
    # Enrich with company name and pattern_param
    print(f"\nEnriching opportunities with company names and pattern params...")
    final_opps = enrich_opportunities(final_opps, RESOURCE_ID, YEARS)
    
    # Archive old CSV and save new one
    print(f"\n--- Saving results ---")
    archive_old_csv(CSV_FILE, ARCHIVE_FOLDER)
    save_to_csv(final_opps, CSV_FILE)
    
    # Print summary
    print(f"\n{'=' * 60}")
    print(f"TOP {len(final_opps)} OPPORTUNITIES (sorted by date):")
    print("=" * 60)
    print(f"{'Date':<12} {'Symbol':<8} {'Company':<20} {'Days':<6} {'Dir':<6} {'AvgP':<8} {'SR':<8} {'TWR':<8} {'Mode'}")
    print("-" * 110)
    
    for opp in final_opps:
        company_short = opp['company_name'][:18] if len(opp['company_name']) > 18 else opp['company_name']
        print(f"{opp['start_date']:<12} {opp['symbol']:<8} {company_short:<20} {opp['days']:<6} {opp['direction']:<6} "
              f"{opp['AvgP']:<8} {opp['SR']:<8} {opp['TWR']:<8} {opp['mode']}")
    
    print(f"\nTotal opportunities saved: {len(final_opps)} ({len(top_consecutive)} consecutive + {len(top_pe)} PE) - ranked by {RANK_BY}, price >= ${MIN_STOCK_PRICE}")