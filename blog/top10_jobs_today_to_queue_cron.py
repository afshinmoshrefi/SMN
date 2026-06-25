# this program sends the jobs to queue for generating all the top 10 blogs
# 1) seasonal report based on top 10 - auto generated 
# 2) Top 10 list for each of the financial groups - auto generated
# 3) Landing for Top 10 lists - 12 buttons with links to 2) - auto generated
# 4) Time Capsule (Archive) page with links to all pages created in 3) - auto generated

# this program should be executed in crontab each night after midnight


# version 2 can create by date


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
import calendar
import matplotlib
import base64
from pprint import pprint
import logging
import subprocess
from blog_tools import assign_years_pyears

sys.path.insert(0, '/home/flask')
import config



#------------------------------------------------------------------------------------------------
# this uses a hack to get the token that changes. this hack is off on prod server. it only works on stage
def get_keyprovider_token():
    url = config.appserver_url+'/login/2/3/4/5/6'
    api_result = requests.get(url)
    # print(url)
    # print(api_result)
    result = api_result.json()
    
    t = result['message'].split(' ')
    return t[4]


#------------------------------------------------------------------------------------------------

# after logging in, the returned token is used to make other calls to the appserver
def login_appserver(keyprovider_token):
    url = config.appserver_url+'/login/28/3/4/5/'+keyprovider_token
    # print('x',url)
    api_result = requests.get(url)
    print(api_result)
    result = api_result.json()
    return result['token']


#------------------------------------------------------------------------------------------------



#------------------------------------------------------------------------------------------------

# get opportunities list for the financial group
def get_opp_list(group_id,financial_groups, month,day,years,pyears,appserver_token):
    
    urlX = config.appserver_url+'/OppList4/'+str(group_id)+'/'+month+'/'+day+'/'+str(years)+'/'+str(pyears)+'/-/0/0?token='+appserver_token
    api_result = requests.get(urlX)
    result = api_result.json()

    return result

#------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------

def diff_between_dates(date2,date1):

    d1 = datetime.datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.datetime.strptime(date2, "%Y-%m-%d")

    difference = d2 - d1
    days = difference.days
    return int(days)

#------------------------------------------------------------------------------------------------


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
            time.sleep(backoff_factor * (2 ** attempt))

#------------------------------------------------------------------------------------------------
def send_jobs_by_opp_date(opp_date,base_year):


    for i, id in enumerate(config.available_resources.keys()):

        # if i == 8: continue # skipping Forex ALL for the top 10 - 

        # if i != 1: continue # testing 

        print(id,'generating blogs for ',config.available_resources[id])

        years,pyears = assign_years_pyears(i) # assigns the pyears that has enough blogs


        symbols_csv = config.available_resources_path[str(id)]
        dfs = pd.read_csv(symbols_csv)[['symbols','name']]

        month=calendar.month_name[int(opp_date[5:7])] # get the month name from the opp_date
        day    = str(int(opp_date[8:]))               # get the day number from the opp_date
        opp_dict = get_opp_list(id,config.available_resources,month,day,years,pyears,appserver_token)

        opp_list = opp_dict['OppList']

        
        count = 0
        for opp in opp_list:
            
            # if opp[1] != 'CDNS':continue
            
            if count >= config.num_opps_per_fi:break 
            
            dh = int(opp[2])  # fixing the issue with my mistake not counting current day so we add 1 - in oppTable its done in react app
            dh = dh + 1
            dh = str(dh)

            resourceID      = id
            symbol          = opp[1]
            date            = opp[0]
            days_hold       = dh
            years           = years
            zero_last_year  = True   # this doesn't show the current year - only custom reports by users sets this to false
            category        = config.category_report
            userid          = 0
            user_level      = 0

            # /-/- are title and slug - used for date range reports creating and deletions
            url = f'{config.blog_queue_server}report/{resourceID}/{symbol}/{date}/{days_hold}/{years}/{base_year}/{zero_last_year}/{category}/{userid}/{user_level}/-/-'
            print(url)
            response=requests.get(url)
            print(response.status_code,response.reason)

            count+=1
        ############################################################
        # create the top10 list pages
        ############################################################
        # i is integer while id is string
        print('creating opp list blog')
        url = f'{config.blog_queue_server}opp_list_blog/{id}/{opp_date}'
        print(url)
        response=requests.get(url)
        print(response.status_code,response.reason)

        print('creating opp list blog with thumbnails')
        url = f'{config.blog_queue_server}opp_list_blog_w_thumbnails/{id}/{opp_date}'
        print(url)
        response=requests.get(url)
        print(response.status_code,response.reason)


    #########################################################################
    # create the 12 marekts page that points to top10 lists for each market
    #########################################################################
    url = f'{config.blog_queue_server}top10_page_by_date/{opp_date}'
    print(url)
    response=requests.get(url)
    print(response.status_code,response.reason)
    
    ############################################################
    # create the top10 page based on Sharpe Ratio
    ############################################################
    url = f'{config.blog_queue_server}top10_page_based_on_sr/{opp_date}'
    print(url)
    response=requests.get(url)
    print(response.status_code,response.reason)

    ############################################################
    # archive
    ############################################################
    url = f'{config.blog_queue_server}archive_list'
    print(url)
    response=requests.get(url)
    print(response.status_code,response.reason)



    # ############################################################
    # # cleanup
    # ############################################################
    # # @app.route('/delete/<string:num_days_old>/<string:category>/<string:slug>/<string:post_id>/<string:max_total>')
    # num_days_old = config.num_days_keep_posts
    # category     = config.category_date_range_report # we have to keep date range reports produced by users in check
    # userid       = '0' # this is used mainly for automated seasonal blogs
    # slug         = ' ' # this is to delete an individual report with this slug
    # post_id      = ' ' # this is teo delete an individual report with a post_id number
    # max_total    = config.max_total # this is the maximum number of date_range_reports that can reside on the wordpress server

    # url = f'{config.blog_queue_server}delete/{num_days_old}/{category}/{userid}/{slug}/{post_id}/{max_total}'
    # print(url)
    # response=requests.get(url)
    # print(response.status_code,response.reason)    
#----------------------------------------------------------------------------------------------------
def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')

#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################


# url = f'{config.blog_queue_server}archive_list'
# print(url)
# response=requests.get(url)
# print(response.status_code,response.reason)
# exit()

# opp blogs are generated with the following variables:
# - opp date for the opportunities typically today
# - opp financial_group - use the id for the group
# - seasonal years years - starting with 10
# - seasonal prob  years - starting with typically 8 except for wilshire its 9 - Bonds are also less
# - image_folder - this is inside the uploads folder in wordpress - tradewave.ai : /var/www/html/wp-content/uploads/p/
# - need to keep track either with wordpress or seperately with a csv file

if __name__ == '__main__':



    # url = f'{config.blog_queue_server}top10_page_by_date/2023-08-07'
    # print(url)
    # response=requests.get(url)
    # print(response.status_code,response.reason)

    # exit()


    # print('creating opp list blog with thumbnails')
    # url = f'{config.blog_queue_server}opp_list_blog_w_thumbnails/1/2023-08-07'
    # print(url)
    # response=requests.get(url)
    # print(response.status_code,response.reason)


    # exit()




    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)

    today_date  = datetime.datetime.now().strftime("%Y-%m-%d")


    # check for feb 29 and if it is increment to march 1
    if today_date[5:] == '02-29':
        today_date = inc_date_day(today_date, 1)




    report_date = today_date
   
    credentials = config.username + ':' + config.password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}

    opp_date = report_date
    base_year = str(datetime.datetime.now().year)
########################################################################################################################
#########################  Send jobs for today to queue - used in crontab running daily       ########################## 
########################################################################################################################


    send_jobs_by_opp_date(today_date,base_year)

    # url = f'{config.blog_queue_server}opp_list_blog/1/2023-05-20'
    # print(url)
    # response=requests.get(url)
    # print(response.status_code,response.reason)

    
