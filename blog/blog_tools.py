# these are shared functions across other python scripts for blog generation

import requests
import pandas as pd
import json
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
import calendar
import matplotlib
import base64
from pprint import pprint
import logging
import subprocess
import urllib.parse
from slugify import slugify
sys.path.insert(0, '/home/flask')
import config


#------------------------------------------------------------------------------------------------------------------------------------------------  
# because this is run in a server it has access to get keyprovider's token directly via intranet
def get_keyprovider_token():
    response = requests.get(config.keystoreURL)
    json = response.json()
    key1 = json['key1']
    key2 = json['key2']

    return key1 
#------------------------------------------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------
# gets default years and pyears based on the 12 markets needs - like 10 of 10 or 8 of 10 or ....
# used by top10_jobs_to_queue.py top10_jobs_to_queue2.py top10_jobs_to_queue3.py generate_blogs.py top10_jobs_today_to_queue_cron.py
def assign_years_pyears(i):

    years  = 10  # this picks what opp table to scan
    pyears = 10   # 8 picks the long one

    if i == 0 or i == 6 or i==7 or i == 11   : pyears = 9 # not enough results when returning 
    if i == 9 or i==5                        : pyears = 8
    if i == 10              :  # this is bonds 
        years = 8
        pyears = 8

    return years,pyears
#------------------------------------------------------------------------------------------------
# returns how many processes are idle and how many are active.  when all 6 is active, don't post any more until it goes down to 2 or 4
# use this before posting a blog to slow down the blog generation
def php_processes():

    # Run php-fpm command with status argument
    result = subprocess.run(['systemctl', 'status', 'php7.4-fpm'], capture_output=True)
    txt=result.stdout.decode('utf-8')
    lines = txt.splitlines()

    # check which line contains the word "Status:" that's the line we want to use
    success = False
    for i in range(len(lines)):
        if 'Status' in lines[i]:
            success = True
            break

    if success == False: # probably have to wait
        return 6,0

    ln = lines[i] # this is the line that shows active and idle processes

    first_comma_index  = ln.index(',',0)
    second_comma_index = ln.index(',',first_comma_index+1)
    first_colon_index =  ln.index(':',0)
    second_colon_index = ln.index(':',first_colon_index+1)
    third_colon_index =  ln.index(':',second_colon_index+1)

    process_active = ln[second_colon_index + 1:first_comma_index].strip()  
    process_idle   = ln[third_colon_index + 1:second_comma_index].strip()  

    return int(process_active),int(process_idle)
#------------------------------------------------------------------------------------------------
# this function sleeps until there is at least 2 idle processes in php available - can be set on config.py
# this is added because we crashed the system for too many posts too quickly
def wait_for_php ():
    interval = config.interval # number of seconds to wait before checking for idle processes

    for i in range(100):  # let's try 100 times and then give up
        process_active,process_idle = php_processes()
        if process_idle <= config.num_idle:
            time.sleep(interval)
        else:
            break

#------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------
# move the blog creation from generate_blogs to here for sharing with the queue function
# when zero_last_year is True, the value of current year is zeroed out.  this is useful when creating an
# old blog and don'e want the current years values to show.  it make it look like it was created on the 
# day of the opportunity
# when zero_list_year is false, the value of current year is kept in tact.  this is useful when
# report generation is initiatied from the Seasonal viewer

def create_blog(title,financial_instruments_group_id,ticker,date1,hold_days,years,zero_last_year):
    pass
#------------------------------------------------------------------------------------------------
def get_all_post_titles_and_ids():


    credentials = config.username + ':' + config.password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}

    page = 1
    all_posts = []

    while True:
        response = requests.get(config.post_endpoint_url + "?per_page=100&_fields=title,slug,id&page={}".format(page), headers=header)
        if response.status_code == 200:
            posts = response.json()
            for post in posts:
                all_posts.append({
                    "title": post["title"]["rendered"],
                    "id": post["id"],
                    "slug": post["slug"]
                })
            if len(posts) < 100:
                break
            page += 1
        else:
            break

    return all_posts
#------------------------------------------------------------------------------------------------
# def search_posts_by_title(title):

#     slug = slugify(title)
#     url = config.domain_root+slug
#     response = requests.get(url)
#     if response.status_code == 200 :
#         return True

#     return False
#------------------------------------------------------------------------------------------------
def get_company_name(resource_id,symbol):

    symbols_csv = config.available_resources_path[str(resource_id)]
    dfs = pd.read_csv(symbols_csv)[['symbols','name']]
    df=dfs[dfs['symbols'] == symbol]

    company = ''
    if df.shape[0] == 1: company = df['name'].iloc[0]

    return company
#------------------------------------------------------------------------------------------------
# def create_title_slug(company, symbol, date1, date2, years,category):
#     if category == config.category_report: # this is a part of top 10 seasonal reports that are auto generated
#         post_title = f"{years}-Year Seasonal Report {company} {symbol} {date1} to {date2}"
#     elif category == config.category_date_range_report: # this is a user generated date range report
#         if len(years)>2 and years == 'pe0':
#             post_title = f"Presidential Election Years Date Range Report {company} {symbol} {date1} to {date2}"
#         elif len(years)>2 and years[:2] == 'pe':
#             post_title = f"Pres Election + {years[2:3]} Years Date Range Report {company} {symbol} {date1} to {date2}"
#         elif years == 'odd':
#             post_title = f"Odd Years Date Range Report {company} {symbol} {date1} to {date2}"
#         elif years == 'even':
#             post_title = f"Even Years Date Range Report {company} {symbol} {date1} to {date2}"
#         else:
#             post_title = f"{years}-Year Date Range Report {company} {symbol} {date1} to {date2}"
#     else:
#         print('error with categories - check category assignment')
#         exit()

#     # create a slug based on the post_title - this is so that we can use it for searching later - overriding auto slug generation
#     post_slug = slugify(post_title)
#     return post_title,post_slug
#------------------------------------------------------------------------------------------------
# this post title is after rebranding
#------------------------------------------------------------------------------------------------
def create_title_slug(company, symbol, date1, date2, years,category):
    if category == config.category_report: # this is a part of top 10 seasonal reports that are auto generated
        post_title = f"{years}-Year TradeWave Report {company} ({symbol}) {date1} to {date2}"
    elif category == config.category_date_range_report: # this is a user generated date range report
        if len(years)>2 and years == 'pe0':
            post_title = f"Presidential Election Years TradeWave Report {company} ({symbol}) {date1} to {date2}"
        elif len(years)>2 and years[:2] == 'pe':
            post_title = f"Pres Election + {years[2:3]} Years TradeWave Report {company} ({symbol}) {date1} to {date2}"
        elif years == 'odd':
            post_title = f"Odd Years TradeWave Report {company} ({symbol}) {date1} to {date2}"
        elif years == 'even':
            post_title = f"Even Years TradeWave Report {company} ({symbol}) {date1} to {date2}"
        else:
            # post_title = f"{years}-Year TradeWave Report {company} ({symbol}) {date1} to {date2}"
            post_title = f"{years}-Year Custom TradeWave Report {company} ({symbol}) {date1} to {date2}"
    else:
        print('error with categories - check category assignment')
        exit()

    # create a slug based on the post_title - this is so that we can use it for searching later - overriding auto slug generation
    post_slug = slugify(post_title)
    return post_title,post_slug
#------------------------------------------------------------------------------------------------
def search_posts_by_slug(slug):

    credentials = config.username + ':' + config.password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}

    url = f'{config.post_endpoint_url}?slug={slug}'
    response = requests.get(url,headers=header)


    if response.ok:
        data = response.json()

        if data :
            return data[0]["id"]  # return the post ID if found
    return -1  # return -1 if not found


#------------------------------------------------------------------------------------------------
def search_posts_by_title(title):

    credentials = config.username + ':' + config.password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}

    slug = slugify(title)
    

    url = f'{config.post_endpoint_url}?slug={slug}'
    response = requests.get(url,headers=header)

    if response.ok:
        data = response.json()
        if data :
            return data[0]["id"]  # return the post ID if found
    return -1  # return -1 if not found
#-------------------------------------------------------------------------------------------------
# convert the 5 paramters that define an opportunity to a single encoded string
def convert_param_base64(financial_group_id,symbol,date1,days_hold,history_years):

    pipe_str = f'{financial_group_id}|{symbol}|{date1}|{days_hold}|{history_years}'
    encoded_string = base64.b64encode(pipe_str.encode()).decode()

    return encoded_string
#-------------------------------------------------------------------------------------------------
# get key1 and key2 from keyprovider
def get_keys(): # get key1 and key2 from keystore url
    response = requests.get(config.keystoreURL)
    json = response.json()
    key1 = json['key1']
    key2 = json['key2']

    return key1,key2
#-------------------------------------------------------------------------------------------------

def get_keyprovider_token():
    # TW2: see create_report.get_keyprovider_token for context. This duplicate
    # exists because thumbnails.py imports it from blog_tools. Returns the
    # SERVICE_API_KEY which login_appserver() exchanges for a real JWT.
    api_key = os.environ.get('SERVICE_API_KEY', '')
    if not api_key:
        raise RuntimeError(
            'SERVICE_API_KEY not set in environment. SMN service requires it to '
            'authenticate with the TW2 appserver via /login/api/<key>.'
        )
    return api_key
#-------------------------------------------------------------------------------------------------
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
#-------------------------------------------------------------------------------------------------
def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')  
#-------------------------------------------------------------------------------------------------
def diff_between_dates(date2,date1):

    d1 = datetime.datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.datetime.strptime(date2, "%Y-%m-%d")

    difference = d2 - d1
    days = difference.days
    return int(days)
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
# action 'add'  'get'
#------------------------------------------------------------------------------------------------
def json_log(log_filename,action,dict):

    dict_list = []
    

    file_exists = os.path.exists(log_filename)

    if action == 'add':
        if file_exists: 
            with open(log_filename, 'r') as f:
                dict_list = json.load(f)
            dict_list.append(dict)
            with open(log_filename, 'w') as f:
                    json.dump(dict_list, f, indent=4)

        else:
            with open(log_filename, 'w') as f:
                    json.dump([dict], f, indent=4)

    if action == 'get': # return the json as a list of dictionaries
        if file_exists:
            with open(log_filename, 'r') as f:
                dict_list = json.load(f)
            return dict_list
#------------------------------------------------------------------------------------------------
def top10_link(id): # id is for financial group
  
  num = 10
  fg = config.available_resources

  today_date = datetime.datetime.now().strftime("%Y-%m-%d")

  post_title = f'Top {num} TradeWave Patterns {fg[str(id)]}  {today_date}-t'  # -t will jump to thumbnails 5/18/2023
  post_slug  = slugify(post_title)

  return config.domain_root+post_slug

#------------------------------------------------------------------------------------------------
def top10_by_sr_title(opp_date):

    title_top10_by_sr      = f'Top 10 TradeWave Opportunities based on Sharpe Ratio on '+opp_date
    title_top10_by_sr_slug = slugify(title_top10_by_sr)
    post_id                = search_posts_by_slug(title_top10_by_sr_slug)

    return title_top10_by_sr,title_top10_by_sr_slug,post_id
#------------------------------------------------------------------------------------------------
def top10_by_market_title(opp_date):

    title_top10_by_market = f'Top 10 Opportunities for each market '+opp_date

    return title_top10_by_market

#------------------------------------------------------------------------------------------------
if __name__ == "__main__":

    json_log('test.json','add',{'ag':'g',})
    print('x')    
    
    # this is for testing - blog_tools is to share functions
    #         "Seasonal Report Micro E-mini DJIA  Index Futures (USA) (YM) 2023-03-03"
    # title = "Seasonal Report Micro E-mini DJIA  Index Futures (USA) (YM) 2023-03-03"
    # x=search_posts_by_title(title)
    # print(x)

    
    # for  y in x:
    #     title = y['title']
    #     slug  = y['slug']
    #     id    = y['id']

    #     s = slugify(title)

    #     if s != slug:
    #         print(id,slug,s)
    #         exit()