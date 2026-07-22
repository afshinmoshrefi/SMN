# 8/8/2023 - rebranded to tradewave.ai 

# I'm adding slugify for python to generate slugs and then search for existance of a post with a slug
# that was generated with slugify 
# I had problem when search for a title that was found using search endpoint
# the problem title was : "Seasonal Report Micro E-mini DJIA  Index Futures (USA) (YM) 2023-03-03"

# 3/5/2023
# realized a flat folder for all the images is gonna be very resource intensive and would reduce the performance 
# making a change for the images folder to add subfolders to improve performance - 
# subfolders will be YEAR/DATE


import math
import re
import requests
import pandas as pd
import os
import sys
from os import listdir
import time
import datetime
from datetime import timedelta
from dateutil.relativedelta import relativedelta
import jwt
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.patches import Rectangle
import matplotlib.dates as mdates
import matplotlib.patches as patches # used to put a white rectangle over the years on the trend chart
import matplotlib
import base64
from pprint import pprint
import logging

from blog_tools import wait_for_php, search_posts_by_title,convert_param_base64,create_title_slug
from slugify import slugify
import json
import urllib.parse

# NOTE: the Pillow white-rectangle patch on the seasonal chart is gone
# (create_seasonals_chart now renders via chartkit, which draws month ticks and
# never year labels), so the PIL import that only served that patch is removed.

matplotlib.use('Agg')

sys.path.insert(0, '/home/flask/blog')
import post_template

sys.path.insert(0, '/home/flask')
import config

#------------------------------------------------------------------------------------------------
def write_to_log (key,url,result):
    res = json.dumps(result)
    print(key)
    print(url)
    print(result)
    print(res)
    print('\n\n')

    # exit()
#------------------------------------------------------------------------------------------------
# TW2: the legacy keyprovider hack (parsing 'Invalid login attempt 6 <token>') no longer works
# because the TW2 appserver enforces aud/iss on /login and never echoes a fresh token in the
# error path. SMN runs as a service and uses the /login/api/<SERVICE_API_KEY> endpoint instead.
#
# To keep the call sites unchanged we keep the same two-function shape:
# get_keyprovider_token() returns the API key (a placeholder -- the real JWT is minted by
# login_appserver()), and login_appserver() exchanges it for the JWT string.
def get_keyprovider_token():
    api_key = os.environ.get('SERVICE_API_KEY', '')
    if not api_key:
        raise RuntimeError(
            'SERVICE_API_KEY not set in environment. SMN service requires it to '
            'authenticate with the TW2 appserver via /login/api/<key>.'
        )
    return api_key


#------------------------------------------------------------------------------------------------

# after logging in, the returned token is used to make other calls to the appserver
def login_appserver(keyprovider_token):
    # TW2 v5: keyprovider_token is the SERVICE_API_KEY supplied by get_keyprovider_token().
    # The key is submitted in the X-Service-Key header via POST /login/api (never in the
    # URL path); the appserver returns the JWT used for subsequent calls.
    url = config.appserver_url.rstrip('/') + '/login/api'
    headers = {'X-Service-Key': keyprovider_token}
    api_result = requests.post(url, headers=headers, timeout=15)
    result = api_result.json()

    if 'token' not in result: # transient failure - rare - try again once
        time.sleep(5)
        api_result = requests.post(url, headers=headers, timeout=15)
        result = api_result.json()
        if 'token' not in result:
            print('login_appserver: failed to obtain token, response:', result)
            return -1
        else:
            print('attempt 2 to login succeeded')

    return result['token']


#------------------------------------------------------------------------------------------------

# this one decodes the token and return the list of financial groups from trade seasonals
def get_financial_groups(appserver_token):
    data = jwt.decode(appserver_token, config.secret_key_appserver,algorithms=['HS256', 'RS256'])
    fgd={}
    for i in range(len(data['resource_disp'])):fgd[i]=data['resource_disp'][i]
    return fgd

#------------------------------------------------------------------------------------------------
def get_chart_data(id,opp_date,symbol,daysOut,years,zero_last_year,appserver_token):

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    urlY = config.appserver_url+'/ChartData4/'+str(id)+'/'+opp_date+'/'+symbol+'/'+daysOut+'/'+str(years)+'?token='+appserver_token
    # print('urlY=',urlY)
#     print('0',id,opp_date,symbol,daysOut,years)
    result = requests.get(urlY)
    # check the current year in case we want to zero out the current year.  unless its from seasonal viewer
    current_year = int(today_date[:4])
    api_result = result.json()

    # check the last item of the list 2/24/2022
    if api_result['ChartData4'][-1]['year'] == current_year:
        if zero_last_year == True:
            api_result['ChartData4'][-1]['price']='0,0'
            api_result['ChartData4'][-1]['pct']= '0,0,0'

    return api_result


#------------------------------------------------------------------------------------------------

# -- helpers for the chartkit-backed legacy report charts --
def _symbol_from_txt(txt):
    """The report captions start with the symbol, e.g. 'NVDA TradeWave ...'."""
    return str(txt).split()[0] if txt else ""

def _window_from_txt(txt):
    """Extract '(YYYY-MM-DD, YYYY-MM-DD)' from a caption like
    'NVDA ... - 2026-07-21 to 2026-09-18'. Returns ('','') if absent."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", str(txt))
    return (m.group(1), m.group(2)) if m else ("", "")


# Reimplemented on chartkit (the approved SMN visual system). Signature and
# return value are unchanged: still returns (barData, barLabel) for the
# cumulative chart. The superseded layout args (x1,y1,txt1,x2,y2,txt2,figsize,
# fontsize) are accepted and ignored — chartkit owns framing/typography now.
# Title inputs are derived from the existing args: symbol and the window dates
# come from txt2; direction is inferred from the data (majority side), matching
# the prototype's own convention.
def create_barchart(chart_data,years,filename,x1,y1,txt1,x2,y2,txt2,figsize,fontsize):
    import chartkit as ck

    barData, barLabel = [], []
    for t in chart_data:
        bar_value = float(t['pct'].split(',')[0])
        barData.append(bar_value)
        barLabel.append(t['year'])

    # Drop the zeroed current-year placeholder ('0,0,0') before rendering.
    years_clean, nets_clean, _, _ = ck._drop_zeroed(barLabel, barData)

    sym = _symbol_from_txt(txt2)
    date1, date2 = _window_from_txt(txt2)
    days = ""
    if date1 and date2:
        try:
            days = diff_between_dates(date2, date1) + 1
        except Exception:
            days = ""
    # direction inferred from the data: majority side (prototype convention)
    wins = sum(1 for v in nets_clean if v > 0)
    direction = "long" if wins >= (len(nets_clean) - wins) else "short"
    meta = dict(symbol=sym, direction=direction, window_start=date1,
                window_end=date2, days=days, variant="bars")
    try:
        ck.record_bars(years_clean, nets_clean, meta, filename)
    except Exception as e:
        print('create_barchart chartkit render failed (non-fatal):', e)

    return barData, barLabel  # return these for use in cumulative return chart
 


#------------------------------------------------------------------------------------------------

def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')  


#------------------------------------------------------------------------------------------------

# Reimplemented on chartkit. Signature/return unchanged (returns None). The
# superseded layout args are accepted and ignored. opp_dir controls the
# compounding direction, exactly as before.
def create_cumulative_chart(barData,barLabel,opp_dir,filename,x1,y1,txt1,x2,y2,txt2,figsize,fontsize):
    import chartkit as ck
    direction = 'short' if str(opp_dir).lower().startswith('s') else 'long'

    # Drop the zeroed current-year placeholder before compounding/rendering.
    years_clean, nets_clean, _, _ = ck._drop_zeroed(
        barLabel, [float(v) for v in barData])

    cumData = []
    cr = 1.0
    for pnet in nets_clean:
        if direction == 'short':
            cr *= (1 + (-pnet / 100.0))
        else:
            cr *= (1 + (pnet / 100.0))
        cumData.append((cr * 100.0) - 100.0)

    sym = _symbol_from_txt(txt2)
    date1, date2 = _window_from_txt(txt2)
    meta = dict(symbol=sym, direction=direction, window_start=date1,
                window_end=date2)
    try:
        ck.cumulative(years_clean, cumData, meta, filename)
    except Exception as e:
        print('create_cumulative_chart chartkit render failed (non-fatal):', e)



#------------------------------------------------------------------------------------------------

def diff_between_dates(date2,date1):

    d1 = datetime.datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.datetime.strptime(date2, "%Y-%m-%d")

    difference = d2 - d1
    days = difference.days
    return int(days)

#------------------------------------------------------------------------------------------------

def get_seasonal_chart_data(id,symbol,years,start_date,token):
    urlC = config.appserver_url+'/consolidated_seasonal_chart2/'+str(id)+'/'+symbol+'/'+str(years)+'/'+start_date+'?token='+token
    api_result = requests.get(urlC)
    result = api_result.json()
    labels  = [x[0] for x in result['cons_seas_chart']]
    seaVals = [x[1] for x in result['cons_seas_chart']]
    return labels,seaVals

# same as get_seasonal_chart_data but passes chart_start_date and opp_start_date separately
# needed for pe0/pe1/pe2/pe3 filtering where the visual start differs from the opportunity date
def get_seasonal_chart_data2(id,symbol,years,chart_start_date,opp_start_date,token):
    urlC = config.appserver_url+'/consolidated_seasonal_chart2/'+str(id)+'/'+symbol+'/'+str(years)+'/'+chart_start_date+'/'+opp_start_date+'?token='+token
    api_result = requests.get(urlC)
    result = api_result.json()
    labels  = [x[0] for x in result['cons_seas_chart']]
    seaVals = [x[1] for x in result['cons_seas_chart']]
    return labels,seaVals

#------------------------------------------------------------------------------------------------
# x,y are coordinates for text
# Reimplemented on chartkit.trend_window (the approved seasonal-path visual).
# The month ticks come from the label strings — no year labels are ever drawn,
# so the old Pillow white-rectangle patch (and its PIL dependency) is gone.
# Signature/return unchanged; superseded layout args accepted and ignored.
# opp_dir is the capitalized direction ('Long'/'Short') from the caller.
def create_seasonals_chart(barLabel,labels,seaVals,date1,date2,opp_dir,filename,x1,y1,txt1,x2,y2,txt2,x3,y3,txt3,figsize,fontsize):
    import chartkit as ck
    direction = 'short' if str(opp_dir).lower().startswith('s') else 'long'

    sym = _symbol_from_txt(txt2)
    # The burned-in "n-year average (first–last)" must count completed years
    # only. When the window end is still in the future, a trailing current-year
    # label is the zeroed placeholder row — exclude it from the claim.
    lbls = [int(y) for y in (barLabel or [])]
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    if lbls and lbls[-1] == datetime.datetime.now().year and str(date2) >= today:
        lbls = lbls[:-1]
    n = len(lbls) if lbls else ""
    year_first = lbls[0] if lbls else ""
    year_last = lbls[-1] if lbls else ""
    try:
        days = diff_between_dates(date2, date1) + 1
    except Exception:
        days = ""
    meta = dict(symbol=sym, direction=direction, n=n, year_first=year_first,
                year_last=year_last, days=days)
    try:
        ck.trend_window(labels, seaVals, date1, date2, direction, meta, filename)
    except Exception as e:
        print('create_seasonals_chart chartkit render failed (non-fatal):', e)




#------------------------------------------------------------------------------------------------
# chat gpt wrote this to convert 2023-02-14 , 2023-06-11 to Feb14-Jun-11
def format_daterange_text(date1, date2):
    # Convert the dates to datetime objects
    d1 = datetime.datetime.strptime(date1, '%Y-%m-%d')
    d2 = datetime.datetime.strptime(date2, '%Y-%m-%d')
    
    # Get the month abbreviations and dates
    month1 = d1.strftime("%b")
    day1 = str(int(d1.strftime("%d")))
    month2 = d2.strftime("%b")
    day2 = str(int(d2.strftime("%d")))
    
    # Concatenate the month abbreviations and dates to form the final string
    result = month1 + day1 + "-" + month2 + day2
    return result
#------------------------------------------------------------------------------------------------
# chat gpt wrote this
# it produces formatted output like 3rd, 22nd, 21st, ... for readable date
#------------------------------------------------------------------------------------------------
def format_date(date_string):
    # parse the date string
    date = datetime.datetime.strptime(date_string, '%Y-%m-%d')
    # get the day of the month
    day = date.day
    # determine the suffix for the day
    if 11 <= day <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    # format the date with the suffix
    formatted_date = date.strftime("%b %d{}".format(suffix)).replace(' 0', ' ')
    return formatted_date

#------------------------------------------------------------------------------------------------
# this request function is made to manage network issues - check linux notes        
def make_request(url, header, post, retries=3, backoff_factor=20):
    for attempt in range(retries):
        try:
            response = requests.post(url, headers=header ,json=post)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            logging.error("Error making API request: {}".format(e))
            if response:
                logging.error("Status code: {}".format(response.status_code))
            if attempt == retries - 1:
                raise e
            wait_for_php()

#------------------------------------------------------------------------------------------------
def get_all_post_titles_and_ids():
    page = 1
    all_posts = []

    while True:
        response = requests.get(config.post_endpoint_url + "?per_page=100&_fields=title,id&page={}".format(page), headers=header)
        if response.status_code == 200:
            posts = response.json()
            for post in posts:
                all_posts.append({
                    "title": post["title"]["rendered"],
                    "id": post["id"]
                })
            if len(posts) < 100:
                break
            page += 1
        else:
            break

    return all_posts

#------------------------------------------------------------------------------------------------
# Create or retrieve the tag ID
#------------------------------------------------------------------------------------------------
def get_or_create_tag(tag_name):

    
    # print(tag_name)
    tag_name = urllib.parse.quote(tag_name)
    # print(tag_name)
    # print('\n\n\n')


    credentials = config.username + ':' + config.password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}


    # Check if the tag already exists
    response = requests.get(config.tags_endpoint_url, headers=header, params={'search': tag_name})
    
    if response.status_code == 200:
        tag_data = response.json()
        if tag_data:
            return tag_data[0]['id']  # Return the first matching tag ID

    # Create the tag if it doesn't exist
    tag_data = {
        'name': tag_name
    }
    

    

    response = requests.post(config.tags_endpoint_url, headers=header, json=tag_data)
    
    if response.status_code == 201:
        created_tag_data = response.json()
        return created_tag_data['id']  # Return the ID of the created tag
    else:
        print(f'Error creating tag: {response.text}')
        return None


#------------------------------------------------------------------------------------------------
# run the report generation
#------------------------------------------------------------------------------------------------
def generate_report(appserver_token,financial_group_id,date1,days_hold,symbol,years,zero_last_year,base_year,category):
    ################ variables ########################

    financial_groups=get_financial_groups(appserver_token)
    #####################################################
    

    domain_root = config.domain_root

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    credentials = config.username + ':' + config.password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}
    fontsize = 14

    days_hold_corrected = str(int(days_hold)-1) # fixes the issue with days = today+hold_days

    # get data from appserver
    cdata=get_chart_data(financial_group_id,date1,symbol,days_hold_corrected,years,zero_last_year,appserver_token)
    
    # print(cdata) # remove
    
    # --------- get company name ---------------------------
    symbols_csv = config.available_resources_path[str(financial_group_id)]
    dfs = pd.read_csv(symbols_csv)[['symbols','name']]
    df=dfs[dfs['symbols'] == symbol]


    company = ''
    if df.shape[0] == 1: company = df['name'].iloc[0]
    #------------------------------------------------------

    date2    = inc_date_day(date1,int(days_hold_corrected))

    # # create post title based on the category
    # if category == config.category_report: # this is a part of top 10 seasonal reports that are auto generated
    #     post_title = f"{years}-Year Seasonal Report {company} {symbol} {date1} to {date2}"
    # elif category == config.category_date_range_report: # this is a user generated date range report
    #     if len(years)>2 and years == 'pe0':
    #         post_title = f"Presidential Election Years Date Range Report {company} {symbol} {date1} to {date2}"
    #     elif len(years)>2 and years[:2] == 'pe':
    #         post_title = f"Pres Election + {years[2:3]} Years Date Range Report {company} {symbol} {date1} to {date2}"
    #     elif years == 'odd':
    #         post_title = f"Odd Years Date Range Report {company} {symbol} {date1} to {date2}"
    #     elif years == 'even':
    #         post_title = f"Even Years Date Range Report {company} {symbol} {date1} to {date2}"
    #     else:
    #         post_title = f"{years}-Year Date Range Report {company} {symbol} {date1} to {date2}"
    # else:
    #     print('error with categories - check category assignment')
    #     exit()

  
    # create a slug based on the post_title - this is so that we can use it for searching later - overriding auto slug generation
    
    post_title,post_slug = create_title_slug(company, symbol, date1, date2, years,category)
    
    # after assembling the title, check if this title exists - if it does skip creating it
    x=search_posts_by_title(post_title)
    ########################################################
    # x=0 # only for debugging
    ########################################################
    
    
    if x > 0 : # this title already exists - skip creation
        print('post for ',symbol,' skipped')
        return x,'skip',post_slug
    

    # print(post_slug)
    # exit()

    # use post_slug to create the chart image filenames
    b_img = 'gain-loss-barchart-'+post_slug+'.png'
    c_img = 'cumulative-return-'+post_slug+'.png'
    s_img = 'trend-chart-'+post_slug+'.png'

    subfolders = f'{base_year}/{date1}/'

    f_barchart = config.chart_root_folder+subfolders
    f_cumchart = config.chart_root_folder+subfolders
    f_seachart = config.chart_root_folder+subfolders
    # create the subfolders
    os.makedirs(f_barchart, exist_ok=True)  # No error raised if directory exists
    os.makedirs(f_cumchart, exist_ok=True)  
    os.makedirs(f_seachart, exist_ok=True)  

    # update to full path of the image file 
    f_barchart = f_barchart+b_img
    f_cumchart = f_cumchart+c_img
    f_seachart = f_seachart+s_img

    
    bar_alt = f'Gain/Loss barchart {symbol} for date range: {date1} to {date2} - this chart shows the gain/loss of the TradeWave opportunity for {symbol} buying on {date1} and selling it on {date2} - this barchart is showing {years} years of history'
    cum_alt = f'Cumulative chart {symbol} for date range: {date1} to {date2} - this chart shows the cumulative return of the TradeWave opportunity date range for {symbol} when bought on {date1} and sold on {date2} - this percent chart shows the capital growth for the date range over the past {years} years ' 
    sea_alt = f'TradeWave Trend Chart {symbol} shows the average trend of the financial instrument over the past {years} years.  Sharp uptrends and downtrends signal a potential TradeWave opportunity'
   
    txt1='TradeWave.AI'
    txt2=symbol+' TradeWave Gain Loss Barchart - '+date1+' to '+date2
    ############################################### Create barchart ################################################
    barData,barLabel=create_barchart(cdata['ChartData4'],years,f_barchart,0.005,0.01,txt1,0.99,0.01,txt2,(14,6),fontsize)
    if date1 == today_date: # remove the current year from cum data
        barData  = barData [:-1]
        barLabel = barLabel[:-1]

    txt1='TradeWave.AI'
    txt2=symbol+' TradeWave Cumulative Return Chart - '+date1+' to '+date2
    ############################################### Create cumulative ##############################################
    opp_dir = cdata['stats']['Trade Dir']
    create_cumulative_chart(barData,barLabel,opp_dir,f_cumchart,0.005,0.01,txt1,0.99,0.01,txt2,(14,6),fontsize)

    

    # calculate the start date of the seasonal chart - its 2 months ago rounded to first day of the month. as long as
    # it continues to be this year.  for the first 3 months of the year,the start date become YYYY-01-01 
    start_date   = str(base_year)+'-01-01'
    two_months_ago = (datetime.datetime.strptime(today_date, '%Y-%m-%d') + relativedelta(months=-2)).strftime('%Y-%m-%d')
    # round 2 months ago to first day of the month
    two_months_ago = two_months_ago[:8]+'01'
    if two_months_ago > start_date: start_date = two_months_ago

    labels,seaVals=get_seasonal_chart_data(financial_group_id,symbol,years,date1,appserver_token)
    txt1='TradeWave.AI'
    txt2=symbol+' '+str(years)+' Year TradeWave Trend Chart'
    txt3=date1+' to '+date2
    ############################################### Create seasonal ################################################
    create_seasonals_chart(barLabel,labels,seaVals,date1,date2,opp_dir,f_seachart,0.005,0.01,txt1,1,0.01,txt2,0.99,0.97,txt3,(14,6),fontsize)


    # cumulative_return = "15453%"
    # formatted_percent = "{:,.0%}".format(int(cumulative_return.rstrip('%')) / 100)
    # print(formatted_percent)

    # calculate biggest winner for both short and long opportunities
    if opp_dir == 'long':
        bw =  max([float(x['pct'].split(',')[0]) for x in cdata['ChartData4']])
    else:
        bw =  -min([float(x['pct'].split(',')[0]) for x in cdata['ChartData4']])

    # print(type(cdata['stats']['Cumulative Return']))
    # exit()

    years_updated = years
    if years_updated[:2] == 'pe':
        years_updated = years_updated.upper()


    opp_param = convert_param_base64(financial_group_id,symbol,date1,days_hold,years_updated)

    if opp_dir == 'long' : 
        summary1 = f'Buy {symbol} on {date1}'
        summary2 = f'Sell {symbol} by {date2}'
        summary3 = f"Average Gain: {cdata['stats']['Avg Profit']}"

        color1 = 'green'
        color2 = 'red'
        color3 = 'MidnightBlue'
    else:                  
        summary1 = f'Sell {symbol} on {date1}'
        summary2 = f'Buy {symbol} by {date2}'
        summary3 = f"Average Gain: {cdata['stats']['Avg Profit']}"

        color1 = 'red'
        color2 = 'green'
        color3 = 'MidnightBlue'

    print('cdata=',cdata)

    new_blog_values = {
        'report_date'         : today_date,
        'symbol'              : symbol,
        'trade_direction'     : opp_dir.capitalize(),
        'date_range_text'     : format_daterange_text(date1, date2),
        'days_hold'           : days_hold,
        'history_years'       : years_updated,
        'securities_group'    : financial_groups[financial_group_id],
        'num_losers'          : cdata['stats']['Num Losers'],
        'num_winners'         : cdata['stats']['Num Winners'],
        'percent_profitable'  : cdata['stats']['Percent Profitable'],
        'biggest_winner'      : bw,
        'avg_loss'            : cdata['stats']['Avg Loss'],
        'avg_profit'          : cdata['stats']['Avg Profit'],
        'median_profit'       : cdata['stats']['Median Profit'],
        'std_dev'             : cdata['stats']['Std Dev'],
        'cumulative_return'   : cdata['stats']['Cumulative Return'],
        'sharpe_ratio'        : cdata['stats']['Sharpe Ratio'],
        'trend_long'          : cdata['stats']['Trend Long'],
        'trend_short'         : cdata['stats']['Trend Short'],
        'date1'               : date1,
        'date2'               : date2,
        'xdate1'              : '<span style="color:red">'+format_date(date1)+'</span>',
        'xdate2'              : '<span style="color:red">'+format_date(date2)+'</span>',
        'domain_root'         : config.domain_root,
        'param'               : opp_param,
        'summary1'            : summary1,
        'summary2'            : summary2,
        'summary3'            : summary3,
        'color1'              : color1,
        'color2'              : color2,
        'color3'              : color3,
        'company'             : company
    }

    
    # these are relative url for the image with domain name in it
    # using post_slug to generate image names
    bar_img = config.img_folder+subfolders+'gain-loss-barchart-'+post_slug+'.png'
    cum_img = config.img_folder+subfolders+'cumulative-return-'+post_slug+'.png'
    sea_img = config.img_folder+subfolders+'trend-chart-'+post_slug+'.png'

    xdate1='<span style="color:red">'+format_date(date1)+'</span>'
    xdate2='<span style="color:red">'+format_date(date2)+'</span>'
    
    html_table = post_template.html_table.format(**new_blog_values)
    content    = post_template.chart_content_for_blog(config.domain_root,bar_img,cum_img,sea_img,bar_alt,cum_alt,sea_alt,symbol,company,format_date(date1),format_date(date2),years)


      # create post title based on the category
    if category == config.category_report: # this is a part of top 10 seasonal reports that are auto generated
         excerpt = f"""
        {symbol} Seasonal Pattern: {format_date(date1)} to {format_date(date2)}. Report contains detailed Charts and statistics about the date range opportunity.
    """ 
    elif category == config.category_date_range_report: # this is a user generated date range report
         excerpt = f"""
        {years}-Year Date Range Report for {company} ({symbol}).  The Report contains detailed charts and stats for {symbol} when purchased on {format_date(date1)} and sold on {format_date(date2)}
    """ 

    #----------------------------------------------------------------------
    # add symbol as a tag to the post - used as focus keyword by rankmath
    #----------------------------------------------------------------------
    
    tag1 = get_or_create_tag(symbol)
    tag2 = get_or_create_tag(company)
    tag3 = get_or_create_tag(date1)
    tags = [tag1,tag2,tag3]
    #----------------------------------------------------------------------
    post = {
        'title'          : post_title,
        'status'         : 'future', 
        'content'        : html_table+content,
        'categories'     : int(category), 
        'date'           : datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        'comment_status' : 'closed',
        'excerpt'        : excerpt,
        'ping_status'    : 'closed',
        'slug'           : post_slug,
        'tags'           : tags
    }
    

    wait_for_php()  # this routine sleeps until there is at least 2 idle processes


    response = make_request(config.post_endpoint_url , header, post)

    if response.status_code == 201:
        print('post for ',symbol,' created')
        x = response.json()
        post_id = x['id']
        return post_id,str(response.status_code),post_slug
    else:
        return -1,str(response.status_code)+' '+response.reason,post_slug

    # return is post_id,message,slug



#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################




# opp blogs are generated with the following variables:
# - opp date for the opportunities typically today but we can make past or futures blogs but stock scores will not be current
# - opp financial_group - use the id for the group
# - seasonal years years - starting with 10
# - seasonal prob  years - starting with typically 8 except for wilshire its 9 - Bonds are also less
# - image_folder - this is inside the uploads folder in wordpress - tradewave.ai : /var/www/html/wp-content/uploads/p/
# - need to keep track either with wordpress or seperately with a csv file

if __name__ == '__main__':



    # x=get_or_create_tag('BA')
    # print(x)
    # exit()


#----------------------------------------------------------------------------
    financial_group_id = 0
    date1 = '2023-09-12'
    days_hold = '200' 
    symbol = 'WMT'
    years  = '10'
    zero_last_year = True
    base_year = str(datetime.datetime.now().year)
    category = config.category_report
#----------------------------------------------------------------------------

    # login 
    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)

    post_id,message,slug=generate_report(appserver_token,financial_group_id,date1,days_hold,symbol,years,zero_last_year,base_year,category)

    print(post_id,message,slug)


