# changed the parameters of 
# run_opp_blog_generation(opp_date):
# added parameter html_table_only boolean
# this is used to only create an html table for each opportunities list

import requests
import pandas as pd
import os.path
from os import listdir
import time
import datetime
from datetime import timedelta
import jwt
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.patches import Rectangle
import calendar
import matplotlib
import base64
import sys
import subprocess
from get_top10_data import load_top10
from slugify import slugify
sys.path.insert(0, '/home/flask')
import config

FACEBOOK_APP_ID = '793368639059153'
FACEBOOK_APP_SECRET = config.FACEBOOK_APP_SECRET
# this is the main page of the TradeWave group
FACEBOOK_PAGE_ID = '110182985091209'  # hard to find - instructions on 5/3/2023 at     https://www.facebook.com/help/1503421039731588
FACEBOOK_OPP_PAGES = {
    0:105060032587168, # dow 30
    1:108908498866288, # nasdaq 100
    2:119837247764175, # S&P500
    3:116984394720187, # Russell 1000
    4:121775350900437, # Wilshire 5000
    5:102907782805350, # indices common
    6:102006882896902, # indices
    7:109340422156287, # futures and commodities
    8:0,               # forex all
    9:102076629555500, # forex liquid
    10:0,              # bonds
    11:102905409472151 # ETF
}
#------------------------------------------------------------------------------------------------
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    filename = config.today_top10_data

    # there are 3 types of tokens in facebook
    # 1 - shored lived access token - good for 1 hour
    # 2 - long lived access token - after obtaining the short lived access token, can then get the long lived access token - good for 60 days
    # 3 - using long lived access token, get then get the page_access_token that has no expiration
    # this info lives in : https://developers.facebook.com/docs/pages/access-tokens
    # content publishing info : https://developers.facebook.com/docs/pages/publishing
    # graph API is used to get the short lived access token: https://developers.facebook.com/tools/explorer/
    # video I used to learn this : https://youtu.be/qI1s_DrzA-o

    # GET THIS FROM FACEBOOK GRAPH api PAGE
    FACEBOOK_SHORT_LIVED_ACCESS_TOKEN = os.environ.get('FACEBOOK_SHORT_LIVED_ACCESS_TOKEN', '')
    # got this token on 5/5/2023 - expires about july 4th
    FACEBOOK_LONG_LIVED_ACCESS_TOKEN = os.environ.get('FACEBOOK_LONG_LIVED_ACCESS_TOKEN', '')

    FACEBOOK_PAGE_ACCESS_TOKEN = os.environ.get('FACEBOOK_PAGE_ACCESS_TOKEN', '')
    
    # GET LONG LIVED TOKEN 60 DAYS
    get_url = f'https://graph.facebook.com/oauth/access_token?grant_type=fb_exchange_token&client_id={FACEBOOK_APP_ID}&client_secret={FACEBOOK_APP_SECRET}&fb_exchange_token={FACEBOOK_SHORT_LIVED_ACCESS_TOKEN}'
    # r = requests.get(get_url)
    # print(r.text)

    # GET PAGE TOKENS 
    get_url = f'https://graph.facebook.com/{FACEBOOK_PAGE_ID}?fields=access_token&access_token={FACEBOOK_LONG_LIVED_ACCESS_TOKEN}'
    print('\n',get_url,'\n')
    r = requests.get(get_url)
    print(r.text)


    # for p in FACEBOOK_OPP_PAGES:
    #     print(p,FACEBOOK_OPP_PAGES[p])
    
    


