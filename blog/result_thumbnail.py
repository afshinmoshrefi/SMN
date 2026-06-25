# I'm adding slugify for python to generate slugs and then search for existance of a post with a slug
# that was generated with slugify 
# I had problem when search for a title that was found using search endpoint
# the problem title was : "Seasonal Report Micro E-mini DJIA  Index Futures (USA) (YM) 2023-03-03"

# 3/5/2023
# realized a flat folder for all the images is gonna be very resource intensive and would reduce the performance 
# making a change for the images folder to add subfolders to improve performance - 
# subfolders will be YEAR/DATE


import math
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
from blog_tools import wait_for_php, search_posts_by_title,convert_param_base64
from slugify import slugify
import json
import urllib.parse
import imgkit
from PIL import Image, ImageDraw, ImageFont



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
# this uses a hack to get the token that changes. this hack is off on prod server. it only works on stage
def get_keyprovider_token():
    url = config.appserver_url+'/login/2/3/4/5/6'
    api_result = requests.get(url)
    # print(url)
    # print(api_result)
    result = api_result.json()
    
    # write_to_log('get_key_provider',url,result)

    t = result['message'].split(' ')
    return t[4]


#------------------------------------------------------------------------------------------------

# after logging in, the returned token is used to make other calls to the appserver
def login_appserver(keyprovider_token):
    url = config.appserver_url+'/login/28/3/4/5/'+keyprovider_token
    api_result = requests.get(url)
    result = api_result.json()

    if 'message' in result: # login failed due to timing - happens less than 0.1% of the time - try again
        time.sleep(10)
        api_result = requests.get(url) # try again
        result = api_result.json()
        if 'message' in result: # should not have happened - possibly due to appserver being down - lets log this message or print it
            print('message:',result['message'])
            return -1
        else:
            print('attempt 2 to login succeeded')

    # write_to_log('login_appserver',url,result)

    return result['token']


#------------------------------------------------------------------------------------------------

# this one decodes the token and return the list of financial groups from TradeWave
def get_financial_groups(appserver_token):
    data = jwt.decode(appserver_token, config.secret_key_appserver,algorithms=['HS256', 'RS256'])
    fgd={}
    for i in range(len(data['resource_disp'])):fgd[i]=data['resource_disp'][i]
    return fgd


#------------------------------------------------------------------------------------------------

# get opportunities list for the financial group
# def get_opp_list(group_id,financial_groups, month,day,years,pyears,appserver_token):
    
#     urlX = appserver_url+'/OppList4/'+str(group_id)+'/'+month+'/'+day+'/'+str(years)+'/'+str(pyears)+'/0/0?token='+appserver_token
#     api_result = requests.get(urlX)
#     result = api_result.json()
#     return result

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

# this version  uses subplots - only way I figured to add % sign to the y labels
def create_barchart(chart_data,years,filename,x1,y1,txt1,x2,y2,txt2,figsize,fontsize):
    barData =[]
    barLabel=[]
    barColor=[]
    green = (0/255, 153/255, 0/255,1)
    red   = (1,0,0,1)




    # print(chart_data)
    print('years=',years)
    # print(chart_data)
    # exit()
    
#     green = (92/255, 184/255, 92/255,1)
#     red   = (217/255, 83/255, 79/255,1)
    
#     green = (0,153/255,0,1)
#     red   = (153/255,0,0,1)

    for i in range(len(chart_data)):
        t=chart_data[i]
        pct = t['pct']
        bar_value = float( pct.split(',')[0] )
        c = green
        if bar_value <0 : c=red

        barData.append(bar_value)
        barLabel.append(t['year'])
        barColor.append(c)

    # now create the barchart
    fig = plt.figure(figsize=figsize,facecolor=(1, 1, 1)) # 1,1,1 shows axes otherwise invisible
    ax = fig.add_subplot(1,1,1)
    ax.tick_params(axis='x', labelsize=20)
    ax.tick_params(axis='y', labelsize=20)
    
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ######################################################
    # Setting barwidths
    ######################################################
    bar_width = 0
    if years == 'odd' or years == 'even':bar_width = 1.5
    if years[:2] == 'pe':bar_width = 3
    # if years == 'pe': bar_width = 0.5
    print('barwidth=',bar_width)
    ######################################################
    

    plt.xticks(barLabel,color='dimgray')
    plt.yticks(color='dimgray')
    ax.axhline(y=0, color='k', linestyle='-',lw=0.5,zorder=0)
    ax.grid(color='darkgray',linewidth=0.5,axis='y')
    ax.axhline(y=0, color='k', linestyle='-',lw=0.5,zorder=0)
    if bar_width > 0 : 
        ax.bar(barLabel,barData,color=barColor,zorder=3,width=bar_width) #control the barwidth for odd, even years and presidential elections PE
    else:
        ax.bar(barLabel,barData,color=barColor,zorder=3)
    
    ax.text(x1,y1,txt1,transform=plt.gcf().transFigure,ha='left',fontsize=fontsize)
    ax.text(x2,y2,txt2,transform=plt.gcf().transFigure,ha='right',fontsize=fontsize)
    

    # only show no more than 10 labels on xaxis
    t = len(chart_data) # total number of labels
    n = math.ceil(t/config.max_labels_to_show)  # this is the interval to show xaxis labels.  1 shows all - 2 is every other - 3 is every 3rd
    [l.set_visible(False) for (i,l) in enumerate(ax.xaxis.get_ticklabels()) if i % n != 0]
    

    plt.savefig(filename)
    plt.close()
    return barData,barLabel # return these for use in cumulative return chart
#------------------------------------------------------------------------------------------------

def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')  
#------------------------------------------------------------------------------------------------
def diff_between_dates(date2,date1):

    d1 = datetime.datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.datetime.strptime(date2, "%Y-%m-%d")

    difference = d2 - d1
    days = difference.days
    return int(days)
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
def result_thumbnail(appserver_token,financial_group_id,date1,days_hold,symbol,years,zero_last_year,base_year,category):
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

    # create post title based on the category
    if category == config.category_report: # this is a part of top 10 seasonal reports that are auto generated
        post_title = f"{years}Y Seasonal Report {company} {symbol} {date1} to {date2}"
    elif category == config.category_date_range_report: # this is a user generated date range report
        if len(years)>2 and years == 'pe0':
            post_title = f"Presidential Election Years Date Range Report {company} {symbol} {date1} to {date2}"
        elif len(years)>2 and years[:2] == 'pe':
            post_title = f"Pres Election + {years[2:3]} Years Date Range Report {company} {symbol} {date1} to {date2}"
        elif years == 'odd':
            post_title = f"Odd Years Date Range Report {company} {symbol} {date1} to {date2}"
        elif years == 'even':
            post_title = f"Even Years Date Range Report {company} {symbol} {date1} to {date2}"
        else:
            post_title = f"{years}Y Date Range Report {company} {symbol} {date1} to {date2}"
    else:
        print('error with categories - check category assignment - result_thumbnail.py line 398')
        exit()

    # create a slug based on the post_title - this is so that we can use it for searching later - overriding auto slug generation
    post_slug = slugify(post_title)

    # result_thumbnails_folder
  
    # result thumbnails are genreated for updated blog posts with winning of a seasonal pattern


    # use post_slug to create the chart image filenames
    b_img = 'gain-loss-barchart-'+post_slug+'.png'

    subfolders = f'{base_year}/{date1}/'

    f_barchart = config.result_thumbnails_folder+subfolders
    # create the subfolders
    os.makedirs(f_barchart, exist_ok=True)  # No error raised if directory exists

    # update to full path of the image file 
    f_barchart = f_barchart+b_img

    
    bar_alt = f'Gain/Loss barchart {symbol} for date range: {date1} to {date2} - this chart shows the gain/loss of the seasonal opportunity for {symbol} buying on {date1} and selling it on {date2} - this barchart is showing {years} years of history'
   
    txt1='tradeseasonals.com'
    txt2=symbol+' Gain Loss Bar Chart - '+date1+' to '+date2
    ############################################### Create barchart ################################################
    barData,barLabel=create_barchart(cdata['ChartData4'],years,f_barchart,0.005,0.01,txt1,0.99,0.01,txt2,(14,6),fontsize)

    # these are relative url for the image with domain name in it
    # using post_slug to generate image names
    bar_img = config.img_folder+subfolders+'gain-loss-barchart-'+post_slug+'.png'

    # print(barData,barLabel,bar_img)
   
    return bar_img,f_barchart

#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################




# opp blogs are generated with the following variables:
# - opp date for the opportunities typically today but we can make past blogs 
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
    financial_group_id = 1
    date1 = '2023-05-13'
    days_hold = '72' 
    symbol = 'AMZN'
    years  = '10'
    zero_last_year = False
    base_year = str(datetime.datetime.now().year)
    category = config.category_date_range_report #  category_date_range_report   category_report
#----------------------------------------------------------------------------

    # login 
    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)

    url,path=result_thumbnail(appserver_token,financial_group_id,date1,days_hold,symbol,years,zero_last_year,base_year,category)

    print(url)
    print(path)


    image = Image.open(path)
    draw = ImageDraw.Draw(image)

    # font_path = 'path_to_font.ttf'  # Replace with the path to your font file
    font_size = 40  # Adjust the font size as needed
    font = ImageFont.truetype('/home/flask/blog/fonts/Roboto-Bold.ttf', font_size)

    text = "Your big text here"

    text_width, text_height = draw.textsize(text, font=font)
    x = (image.width - text_width) // 2
    y = 10  # Adjust the y-coordinate as needed to position the text

    draw.text((x, y), text, font=font, fill=(0, 0, 0))  # Adjust the fill color as needed

    image.save(path)

