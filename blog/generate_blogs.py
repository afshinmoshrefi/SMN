
#!/usr/bin/env python
# coding: utf-8
#   2/2/2023

# - login to staging appserver
# - list of financial groups is downloaded
# - get all opps for a financial group
# - for each opp create:
#   - get barchart data
#   - get opportunity stats
#   - create barchart and save it
#   - create cumulative chart and save it
#   - create seasonal chart and save it
# - calculate startdate of seasonal chart to 2 months ago rounded to first day of the month
# 2/6/2023
# - next is to make this a program that can run as a .py 
# In[1]:


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

# matplotlib.use('Agg')

sys.path.insert(0, '/home/flask/blog')
import post_template

sys.path.insert(0, '/home/flask')
import config



#------------------------------------------------------------------------------------------------
# this uses a hack to get the token that changes. this hack is off on prod server. it only works on stage
def get_keyprovider_token():
    url = appserver_url+'/login/2/3/4/5/6'
    api_result = requests.get(url)
    # print(url)
    # print(api_result)
    result = api_result.json()
    
    t = result['message'].split(' ')
    return t[4]


#------------------------------------------------------------------------------------------------

# after logging in, the returned token is used to make other calls to the appserver
def login_appserver(keyprovider_token):
    url = appserver_url+'/login/28/3/4/5/'+keyprovider_token
    # print('x',url)
    api_result = requests.get(url)
    result = api_result.json()
    return result['token']


#------------------------------------------------------------------------------------------------

# this one decodes the token and return the list of financial groups from TradeWave
def get_financial_groups(appserver_token):
    data = jwt.decode(appserver_token, secret_key_appserver,algorithms=['HS256', 'RS256'])
    fgd={}
    for i in range(len(data['resource_disp'])):fgd[i]=data['resource_disp'][i]
    return fgd


#------------------------------------------------------------------------------------------------

# get opportunities list for the financial group
def get_opp_list(group_id,financial_groups, month,day,years,pyears,appserver_token):
    
    urlX = appserver_url+'/OppList4/'+str(group_id)+'/'+month+'/'+day+'/'+str(years)+'/'+str(pyears)+'/-/0/0?token='+appserver_token
    api_result = requests.get(urlX)
    result = api_result.json()
    return result

#------------------------------------------------------------------------------------------------


def get_chart_data(id,opp_date,symbol,daysOut,years,zero_last_year,appserver_token):
    urlY = appserver_url+'/ChartData4/'+str(id)+'/'+opp_date+'/'+symbol+'/'+daysOut+'/'+str(years)+'?token='+appserver_token
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
def create_barchart(chart_data,filename,x1,y1,txt1,x2,y2,txt2,figsize,fontsize):
    barData =[]
    barLabel=[]
    barColor=[]
    
    green = (0/255, 153/255, 0/255,1)
    red   = (1,0,0,1)
    
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
    plt.xticks(barLabel,color='dimgray')
    plt.yticks(color='dimgray')

    
    ax.axhline(y=0, color='k', linestyle='-',lw=0.5,zorder=0)
    ax.grid(color='darkgray',linewidth=0.5,axis='y')
    ax.axhline(y=0, color='k', linestyle='-',lw=0.5,zorder=0)
    ax.bar(barLabel,barData,color=barColor,zorder=3)
    ax.text(x1,y1,txt1,transform=plt.gcf().transFigure,ha='left',fontsize=fontsize)
    ax.text(x2,y2,txt2,transform=plt.gcf().transFigure,ha='right',fontsize=fontsize)
    plt.savefig(filename)
    plt.close()
    return barData,barLabel # return these for use in cumulative return chart
 


#------------------------------------------------------------------------------------------------

def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')  


#------------------------------------------------------------------------------------------------

def create_cumulative_chart(barData,barLabel,opp_dir,filename,x1,y1,txt1,x2,y2,txt2,figsize,fontsize):
    cumData = []
    cr=1
    for p in barData:
        if opp_dir == 'Short': cr *= (1+(-p/100))
        else                 : cr *= (1+(p/100))                 
        cumData.append((cr*100)-100)
    
    fig = plt.figure(figsize=figsize,facecolor=(1, 1, 1)) # 1,1,1 shows axes otherwise invisible
    ax = fig.add_subplot(1,1,1)
    ax.tick_params(axis='x', labelsize=20)
    ax.tick_params(axis='y', labelsize=20)
    
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.xticks(barLabel,color='dimgray')
    plt.yticks(color='dimgray')
    ax.plot(barLabel,cumData)
    ax.grid(color='darkgray',linewidth=0.5)
    ax.text(x1,y1,txt1,transform=plt.gcf().transFigure,ha='left',fontsize=fontsize)
    ax.text(x2,y2,txt2,transform=plt.gcf().transFigure,ha='right',fontsize=fontsize)
    plt.savefig(filename)
    plt.close()


#------------------------------------------------------------------------------------------------

def diff_between_dates(date2,date1):

    d1 = datetime.datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.datetime.strptime(date2, "%Y-%m-%d")

    difference = d2 - d1
    days = difference.days
    return int(days)

#------------------------------------------------------------------------------------------------

def get_seasonal_chart_data(id,symbol,years,start_date,token):
    urlC = appserver_url+'/consolidated_seasonal_chart2/'+str(id)+'/'+symbol+'/'+str(years)+'/'+start_date+'?token='+token
    api_result = requests.get(urlC)
    result = api_result.json()
    labels  = [x[0] for x in result['cons_seas_chart']]
    seaVals = [x[1] for x in result['cons_seas_chart']]
    return labels,seaVals

#------------------------------------------------------------------------------------------------


# x,y are coordinates for text
def create_seasonals_chart(barLabel,labels,seaVals,date1,date2,opp_dir,filename,x1,y1,txt1,x2,y2,txt2,x3,y3,txt3,figsize,fontsize):
    
    opp_color = 'green'
    if opp_dir == 'Short':opp_color = 'red'
    
    fig = plt.figure(figsize=figsize,facecolor=(1, 1, 1)) # 1,1,1 shows axes otherwise invisible
    
#     fig.set_facecolor((0.99,0.9,0.9))
    
    ax = fig.add_subplot(1,1,1)
    ax.tick_params(axis='y', labelsize=20)
    
    plt.subplots_adjust(bottom=0.2)
    
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.xticks(barLabel,color='dimgray',rotation=90)
    plt.yticks(color='dimgray')
    ax.plot(labels,seaVals)
    ax.grid(color='gray',linewidth=0.5,linestyle=':')
    ax.set_xticks(labels[::6]);
    ax.set_xticklabels(labels[::6],fontsize=10);
    
    # calc how many days between first date on chart and oppdate1
    oppd1=diff_between_dates(date1,labels[0])
    oppd2=diff_between_dates(date2,date1) # this is really daysOut
    
    rect = Rectangle((oppd1, 0), oppd2, 100, alpha=0.1,fill=True,facecolor=opp_color,edgecolor='black',linewidth=2)
    ax.add_patch(rect)
#     ax.text(x1,y1,txt1,transform=ax.transAxes,ha='left',fontsize=fontsize)
#     ax.text(x2,y2,txt2,transform=ax.transAxes,ha='right',fontsize=fontsize)
    ax.text(x1,y1,txt1,transform=plt.gcf().transFigure,ha='left',fontsize=fontsize)
    ax.text(x2,y2,txt2,transform=plt.gcf().transFigure,ha='right',fontsize=fontsize)
    ax.text(x3,y3,txt3,transform=plt.gcf().transFigure,ha='right',fontsize=fontsize)
    
    plt.savefig(filename)
    plt.close()


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
def generate_post_images_content(opp_list,num_opps_per_fi ,img_folder,sg,years,base_year,post_endpoint_url,post_date,id,header):
    
    securities_group_display = sg # might have to shorten sg to fit better 
    
    tmp_opp_list = opp_list
    if len(tmp_opp_list)>num_opps_per_fi:
        tmp_opp_list=opp_list[:num_opps_per_fi] # just select the top 10 or whatever num_opps_per_fi is

    num_created = 0

    

    for opp in tmp_opp_list:
    
        opp_date1    = opp[0]
        opp_symbol   = opp[1]
        opp_daysout  = opp[2]
        opp_dir      = opp[3]

        

        df=dfs[dfs['symbols'] == opp_symbol]
        company = ''
        if df.shape[0] == 1: company = df['name'].iloc[0]

        # post_title = f'Seasonal Pattern  {company} ({opp_symbol}) '+post_date[:-9]
        post_title = f"Seasonal Pattern  {company} ({opp_symbol}) "+opp_date1


        # if opp_symbol == 'MCD': # delete
        #     print(all_posts_titles_list)
        #     print('')
        #     print(post_title)

        if post_title in all_posts_titles_list: # this one has already been published
            print('post for ',opp_symbol,' skipped')
            continue

        num_created = num_created +1

        opp_date2    = inc_date_day(opp_date1,int(opp_daysout))

        # filenames for the 3 charts
        f_barchart = folder+'gain-loss-barchart-'+opp_symbol+'-seasonal-years-'+str(years)+'-daterange-'+opp_date1+'-to-'+opp_date2+'_'+str(id)+'.png'
        f_cumchart = folder+'cumulative-return-' +opp_symbol+'-seasonal-years-'+str(years)+'-daterange-'+opp_date1+'-to-'+opp_date2+'_'+str(id)+'.png'
        f_seachart = folder+'trend-chart-'+opp_symbol+'-baseyear-'+today_date[:4]+'-seasonal-years-'+str(years)+         '_'+str(id)+'.png'



        fontsize = 14  

#########################################################################################################################################
        zero_last_year = True # zeros out this year when report is created late - should be false when generated from seasonal viewer
#########################################################################################################################################

        cdata=get_chart_data(id,opp_date1,opp_symbol,opp_daysout,years,zero_last_year,appserver_token)


        txt1='tradeseasonals.com'
        txt2=opp_symbol+' Gain Loss Bar Chart - '+opp_date1+' to '+opp_date2
        ############################################### Create barchart ################################################
        barData,barLabel=create_barchart(cdata['ChartData4'],f_barchart,0.005,0.01,txt1,0.99,0.01,txt2,(14,6),fontsize)
        if opp_date1 == today_date: # remove the current year from cum data
            barData  = barData [:-1]
            barLabel = barLabel[:-1]

        txt1='tradeseasonals.com'
        txt2=opp_symbol+' Cumulative Return Chart - '+opp_date1+' to '+opp_date2
        ############################################### Create cumulative ##############################################
        create_cumulative_chart(barData,barLabel,opp_dir,f_cumchart,0.005,0.01,txt1,0.99,0.01,txt2,(14,6),fontsize)


        # calculate the start date of the seasonal chart - its 2 months ago rounded to first day of the month. as long as
        # it continues to be this year.  for the first 3 months of the year,the start date become YYYY-01-01 
        current_year = datetime.datetime.now().year
        start_date   = str(current_year)+'-01-01'
    #     today_date = datetime.datetime.now().strftime("%Y-%m-%d")
        two_months_ago = (datetime.datetime.strptime(today_date, '%Y-%m-%d') + relativedelta(months=-2)).strftime('%Y-%m-%d')
        # round 2 months ago to first day of the month
        two_months_ago = two_months_ago[:8]+'01'
        if two_months_ago > start_date: start_date = two_months_ago
        start_date


        labels,seaVals=get_seasonal_chart_data(id,opp_symbol,years,start_date,appserver_token)
        txt1='tradeseasonals.com'
        txt2=opp_symbol+' '+str(years)+' Year Average Seasonal Chart'
        txt3=opp_date1+' to '+opp_date2
        ############################################### Create seasonal ################################################
        create_seasonals_chart(barLabel,labels,seaVals,opp_date1,opp_date2,opp_dir,f_seachart,0.005,0.01,txt1,1,0.01,txt2,0.99,0.97,txt3,(14,6),fontsize)


        # evaluate the 2 fstring to create the blog :  html_table and content
        
        
        new_blog_values = {
            'report_date'         : report_date,
            'symbol'              : opp_symbol,
            'trade_direction'     : opp_dir,
            'date_range_text'     : format_daterange_text(opp_date1, opp_date2),
            'days_hold'           : opp_daysout,
            'history_years'       : years,
            'securities_group'    : securities_group_display,
            'num_losers'          : cdata['stats']['Num Losers'],
            'num_winners'         : cdata['stats']['Num Winners'],
            'percent_profitable'  : cdata['stats']['Percent Profitable'],
            'biggest_winner'      : max([float(x['pct'].split(',')[0]) for x in cdata['ChartData4']]),
            'avg_loss'            : cdata['stats']['Avg Loss'],
            'avg_profit'          : cdata['stats']['Avg Profit'],
            'median_profit'       : cdata['stats']['Median Profit'],
            'std_dev'             : cdata['stats']['Std Dev'],
            'cumulative_return'   : cdata['stats']['Cumulative Return'],
            'sharpe_ratio'        : cdata['stats']['Sharpe Ratio'],
            'long_score'          : cdata['stats']['Trend Long'],
            'short_score'         : cdata['stats']['Trend Short'],
            'date1'               : opp_date1,
            'date2'               : opp_date2,
            'xdate1'              : '<span style="color:red">'+format_date(opp_date1)+'</span>',
            'xdate2'              : '<span style="color:red">'+format_date(opp_date2)+'</span>',
            'domain_root'         : domain_root
        }
        
        

        bar_img = img_folder + f'gain-loss-barchart-{opp_symbol}-seasonal-years-{years}-daterange-{opp_date1}-to-{opp_date2}_{str(id)}.png'
        cum_img = img_folder + f'cumulative-return-{opp_symbol}-seasonal-years-{years}-daterange-{opp_date1}-to-{opp_date2}_{str(id)}.png'
        sea_img = img_folder + f'trend-chart-{opp_symbol}-baseyear-{base_year}-seasonal-years-{years}_{str(id)}.png'

        bar_alt = f'Gain/Loss barchart {opp_symbol} for date range: {opp_date1} to {opp_date2} - this chart shows the gain/loss of the seasonal opportunity for {opp_symbol} buying on {opp_date1} and selling it on {opp_date2} - this barchart is showing {years} years of history'
        cum_alt = f'Cumulative chart {opp_symbol} for date range: {opp_date1} to {opp_date2} - this chart shows the cumulative return of the seasonal opportunity date range for {opp_symbol} when bought on {opp_date1} and sold on {opp_date2} - this percent chart shows the capital growth for the date range over the past {years} years ' 
        sea_alt = f'Average Seasonal Chart {opp_symbol} shows the average trend of the financial instrument over the past {years} years.  Sharp uptrends and downtrends signal a potential seasonal opportunity'
        
        xdate1='<span style="color:red">'+format_date(opp_date1)+'</span>'
        xdate2='<span style="color:red">'+format_date(opp_date2)+'</span>'
        
        html_table = post_template.html_table.format(**new_blog_values)
        content    = post_template.chart_content_for_blog(domain_root,bar_img,cum_img,sea_img,bar_alt,cum_alt,sea_alt,opp_symbol,company,format_date(opp_date1),format_date(opp_date2))


        excerpt = f"""
            {opp_symbol} Seasonal Pattern: {format_date(opp_date1)} to {format_date(opp_date2)}. Report contains detailed Charts and statistics about the seasonal opportunity.
        """ 

        post = {
            'title'          : post_title,
            'status'         : 'future', 
            'content'        : html_table+content,
            'categories'     : config.category_report, 
            'date'           : post_date,
            'comment_status' : 'closed',
            'excerpt'        : excerpt,
            'ping_status'    : 'closed',
        }
        
        wait_for_php()  # this routine sleeps until there is at least 2 idle processes
        
        response = make_request(post_endpoint_url , header, post)
        
        if response.status_code == 201:
            print('post for ',opp_symbol,' created')
        else:
            print(response.status_code, response.reason)

        time.sleep(1)

    return num_created
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
# returns how many processes are idle and how many are active.  when all 6 is active, don't post any more until it goes down to 2 or 4
# use this before posting a blog to slow down the blog generation
def php_processes():

    # Run php-fpm command with status argument
    result = subprocess.run(['systemctl', 'status', 'php7.4-fpm'], capture_output=True)
    txt=result.stdout.decode('utf-8')
    lines = txt.splitlines()

    ln = lines[5] # this is the line that shows active and idle processes

    first_comma_index  = ln.index(',',0)
    second_comma_index = ln.index(',',first_comma_index+1)
    first_colon_index =  ln.index(':',0)
    second_colon_index = ln.index(':',first_colon_index+1)
    third_colon_index =  ln.index(':',second_colon_index+1)

    process_active = ln[second_colon_index + 1:first_comma_index].strip()  
    process_idle   = ln[third_colon_index + 1:second_comma_index].strip()  

    return int(process_active),int(process_idle)
#------------------------------------------------------------------------------------------------
# this function sleeps until there is at least 2 idle processes in php available - 
# this is added because we crashed the system for too many posts too quickly
def wait_for_php ():
    interval = config.interval # number of seconds to wait before checking for idle processes

    for i in range(100):  # let's try 100 times and then give up
        process_active,process_idle = php_processes()
        if process_idle <= config.num_idle:
            time.sleep(interval)
        else:
            break

#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################




# opp blogs are generated with the following variables:
# - opp date for the opportunities typically today
# - opp financial_group - use the id for the group
# - seasonal years years - starting with 10
# - seasonal prob  years - starting with typically 8 except for wilshire its 9 - Bonds are also less
# - image_folder - this is inside the uploads folder in wordpress - tradewave.ai : /var/www/html/wp-content/uploads/p/
# - need to keep track either with wordpress or seperately with a csv file

if __name__ == '__main__':

    ################ variables ########################
    appserver_url = config.appserver_url
    secret_key_appserver = config.secret_key_appserver
    font = config.font
    post_endpoint_url = config.post_endpoint_url
    # staging blog username set as author
    username=config.username
    password=config.password
    img_folder = config.img_folder # location of where the blog images are located
    symbols_csv_folder = config.symbols_csv_folder # gets company names from these files
    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)
    financial_groups=get_financial_groups(appserver_token)
    #####################################################
    
    domain_root = config.domain_root

    delay = config.delay # number of seconds between each financial group creation

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    if len(sys.argv) == 1 : 
        print('ver 0.1 - arguments are either "list" or "id=#" for individual groups & id=all will create all for today - optional date=YYYY-MM-DD')
        exit()  # need a parameter

    if sys.argv[1].lower() =='list': 
        pprint(financial_groups)
        exit()

    if 'id' not in sys.argv[1]: 
        print('arguments are either "list" or "id=#" ')
        exit()
    tmp = sys.argv[1].split('=')

    # report date is either today or for a date that is passed in as an argument
    report_date = today_date
    if len(sys.argv) == 3 :
        x = sys.argv[2]
        if '=' in x:
            y = x.split('=')
            if y[0]=='date':
                report_date = y[1]
            else:
                print('second argument must be date=YYYY-MM-DD')
                exit()
        else:
            report_date = x

    # check day of the week - skip creation if its sunday or monday
    # they are the same as saturday
    d=datetime.datetime.strptime(report_date, "%Y-%m-%d").weekday()
    if d == 6 or d == 0 : # sunday or monday
        print('skipping creation for sunday or monday - its the same as saturday')
        exit()



    credentials = username + ':' + password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}

    num_opps_per_fi     = config.num_opps_per_fi # how many of the opportuniteis to generate blogs for

    
    base_year = str(datetime.datetime.now().year)

    # post_date = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    post_date = report_date+'T05:00:00' # 5AM is the post time

    # opp_date by default is today, can override here
    opp_date = report_date
    folder = config.folder

    all_posts_titles_ids_list=get_all_post_titles_and_ids()
    # these are titles of the published posts - use it to not publish a title that has already been published
    
    all_posts_titles_list = [x['title'].replace('&amp;','&').replace("&#8217;","'") for x in all_posts_titles_ids_list] 
   
    t1=time.time()

    print('Generate Blog for ',report_date)

    for i, id in enumerate(financial_groups.keys()):

        if tmp[1].lower() != 'all':
            ix = int(tmp[1])
            if id != ix: continue 

        print(id,'generating blogs for ',financial_groups[id])

        years,pyears = assign_years_pyears(i) # assigns the pyears that has enough blogs

        symbols_csv = config.available_resources_path[str(id)]

        dfs = pd.read_csv(symbols_csv)[['symbols','name']]

        month=calendar.month_name[int(opp_date[5:7])] # get the month name from the opp_date
        day    = str(int(opp_date[8:]))               # get the day number from the opp_date
        opp_dict = get_opp_list(id,financial_groups,month,day,years,pyears,appserver_token)

        opp_list = opp_dict['OppList']

        # print(opp_list)
        # exit(0)

        num_created=generate_post_images_content(opp_list,num_opps_per_fi,img_folder,financial_groups[id],years,base_year,post_endpoint_url,post_date,id,header)
        # only sleep if at least 5 blogs are made otherwise sleep only 2 seconds
        print('num_created =',num_created)



        # if i < len(financial_groups) - 1:
        #     if num_created > 4 and tmp[1].lower() == 'all': time.sleep(delay) # 3 minute wait seems to make all child processes for php back to idle
        #     else :              time.sleep(2)
        #     print('')


        # get list of posts again after the last set of posts were created
        all_posts_titles_ids_list=get_all_post_titles_and_ids()
        all_posts_titles_list = [x['title'].replace('&amp;','&').replace("&#8217;","'") for x in all_posts_titles_ids_list] 

    t2=time.time()
    print(round(t2-t1,2),'seconds',round((t2-t1)/60,2),'minutes')