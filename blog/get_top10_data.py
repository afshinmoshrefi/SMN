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
from blog_tools import wait_for_php, search_posts_by_title,assign_years_pyears
import pprint

from slugify import slugify
sys.path.insert(0, '/home/flask')
import config

# this uses a hack to get the token that changes. this hack is off on prod server. it only works on stage
def get_keyprovider_token():
    url = config.appserver_url+'/login/2/3/4/5/6'
    api_result = requests.get(url)
    result = api_result.json()
    t = result['message'].split(' ')
    return t[4]


#--------------------------------------------------------------------------------------------------------------------------

# after logging in, the returned token is used to make other calls to the appserver
def login_appserver(keyprovider_token):
    url = config.appserver_url+'/login/28/3/4/5/'+keyprovider_token
    api_result = requests.get(url)

    result = api_result.json()

    return result['token']


#--------------------------------------------------------------------------------------------------------------------------

# this one decodes the token and return the list of financial groups from trade seasonals
def get_financial_groups(appserver_token):
    data = jwt.decode(appserver_token, config.secret_key_appserver,algorithms=['HS256', 'RS256'])
    fgd={}
    for i in range(len(data['resource_disp'])):fgd[i]=data['resource_disp'][i]
    return fgd


#--------------------------------------------------------------------------------------------------------------------------

# get opportunities list for the financial group
def get_opp_list(group_id, month,day,years,pyears,appserver_token):
    
    urlX = config.appserver_url+'/OppList4/'+str(group_id)+'/'+month+'/'+day+'/'+str(years)+'/'+str(pyears)+'/-/0/0?token='+appserver_token
    api_result = requests.get(urlX)
    result = api_result.json()
    return result


#--------------------------------------------------------------------------------------------------------------------------

def get_chart_data(id,opp_date,symbol,daysOut,years,appserver_token):
    urlY = config.appserver_url+'/ChartData4/'+str(id)+'/'+opp_date+'/'+symbol+'/'+daysOut+'/'+str(years)+'?token='+appserver_token
    result = requests.get(urlY)
    if result.status_code > 201 : print('get_chart_data returned',result.status_code,result.text,result.reason)
    api_result = result.json()
    return api_result

#--------------------------------------------------------------------------------------------------------------------------

def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')  

#--------------------------------------------------------------------------------------------------------------------------

def diff_between_dates(date2,date1):

    d1 = datetime.datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.datetime.strptime(date2, "%Y-%m-%d")

    difference = d2 - d1
    days = difference.days
    return int(days)

#--------------------------------------------------------------------------------------------------------------------------

def get_opp_list_for_display(num_in_list,id,month,day,years,pyears,appserver_token):  # num_in_list = 10 would return top 10 opportunities

    opp_dict=get_opp_list(id,month,day,years,pyears,appserver_token)


    num_opps = len(opp_dict['OppList'])

    if num_opps > num_in_list: num_opps = num_in_list 
    top_opps=opp_dict['OppList'][:num_in_list] # top 10 list of lists
    df=pd.DataFrame(top_opps,columns=['Date','Symbol','DaysOut','Direction','Sharpe Ratio','avg_profit','median_profit','cumulative_return','stddev'])
    
   
    
    # add 5 columns to the top 10 list by calling et_chart_data for each opporutnity - same as clicking an opp in the dashboard
    cumulative = []
    avg_profit = []
    long_score = []
    short_score= []
    end_date   = []

    # adding on 9/17/2023
    stddev         = []
    num_losers     = []
    num_winners    = []
    pct_profitable = []
    biggest_winner = []
    avg_loss       = []
    last_trade_date= []



    for i,r in df.iterrows():
        time.sleep(0.3)




        # fix the issue about not counting today - correction is made inside oppTable in react - should be done here too
        days_out = r['DaysOut']
        # days_out = int(r['DaysOut'])
        # days_out = days_out +1 
        # days_out = str(days_out)


        x=get_chart_data(id,r['Date'],r['Symbol'],days_out,years,appserver_token)


        # figure out the biggest winner
        plist = x['ChartData4']
        bw = 0 # biggest_winner
        if r['Direction'] == 'Short': bw=10000
        
        for y in plist:
          
            g=float(y['pct'].split(',')[0])
          
            if r['Direction'] == 'Long' and g>bw:bw=g
            if r['Direction'] == 'Short' and g<bw:bw=g

        
        if r['Direction'] == 'Short': bw=-bw

    #     print(i,x['stats']['Cumulative Return'],x['stats']['Avg Profit'],x['stats']['Long Score'],x['stats']['Short Score'])
        cumulative.append(x['stats']['Cumulative Return'])
        avg_profit.append(x['stats']['Avg Profit'])
        long_score.append(x['stats']['Trend Long'])
        short_score.append(x['stats']['Trend Short'])


        stddev.append(x['stats']['Std Dev'])         
        num_losers.append(x['stats']['Num Losers'])         
        num_winners.append(x['stats']['Num Winners'])        
        pct_profitable.append(x['stats']['Percent Profitable'])     
        biggest_winner.append(f'{bw}%')     
        avg_loss.append(x['stats']['Avg Loss'])           
        last_trade_date.append(x['stats']['last_trade_date']) # this is used to filter out non trading securities due to sale, merger or jist dropped out of our data source

        end_date.append(inc_date_day(r['Date'],int(r['DaysOut'])))  # -1 is the correction

    df['Date2'] = end_date
    df['Cumulative Return'] = cumulative
    df['Avg Profit'] = avg_profit
    df['Trend Long'] = long_score # name of short score and long score changed to trend long and trend short 8/12/2023
    df['Trend Short'] = short_score

    # column 'DaysOut' need to be incremented by 1 based on changes I made to the data a few months ago to match the opp list
    df['DaysOut'] = (df['DaysOut'].astype(int) + 1).astype(str)
        
    # adding on 9/17/2023
    df['stddev'] = stddev      
    df['num_losers'] = num_losers    
    df['num_winners'] = num_winners     
    df['pct_profitable'] = pct_profitable  
    df['biggest_winner'] = biggest_winner
    df['avg_loss'] = avg_loss

    df['last_trade_date'] = last_trade_date


    return df

#--------------------------------------------------------------------------------------------------------------------------
def get_slugs_for_opps(id,df,dfs,years,base_year): # domain_root used for hrefs in <a> - dfs has the company names used for slug

    post_titles,companies,slugs,bar_imgs,cum_imgs,sea_imgs = [],[],[],[],[],[]

    for i,r in df.iterrows():

        # create the URL for the report for the anchors
        symbol = r['Symbol']
        dfx=dfs[dfs['symbols'] == symbol]
        company = ''
        if dfx.shape[0] == 1: 
            company = dfx['name'].iloc[0]

        post_title = f"{years}-Year TradeWave Report {company} ({symbol}) {r['Date']} to {r['Date2']}"
        post_slug = slugify(post_title)

        report_url = f'{config.domain_root}{post_slug}'
        

        subfolders = f'{base_year}/{r["Date"]}/'

        bar_img = config.img_folder+subfolders+'gain-loss-barchart-'+post_slug+'.png'
        cum_img = config.img_folder+subfolders+'cumulative-return-'+post_slug+'.png'
        sea_img = config.img_folder+subfolders+'trend-chart-'+post_slug+'.png'

        companies.append(company)
        slugs.append(report_url)
        bar_imgs.append(bar_img)
        cum_imgs.append(cum_img)
        sea_imgs.append(sea_img)
        post_titles.append(post_title)
       
    return companies,slugs,bar_imgs,cum_imgs,sea_imgs,post_titles
    
#--------------------------------------------------------------------------------------------------------------------------

#----------------------------------------------------------------------------        


#------------------------------------------------------------------------------------------------r4f4

def find_nth_occurrence(string, substring, occurrence_number):
    start = string.find(substring)
    while start >= 0 and occurrence_number > 1:
        start = string.find(substring, start+len(substring))
        occurrence_number -= 1
    return start

#------------------------------------------------------------------------------------------------
# run the opp blog generate
# html_tables_only is an option parameter used to create html tables used for home page and social media postings
def get_top10_info(opp_date):

    ################ variables ########################
    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)
    financial_groups=get_financial_groups(appserver_token)
    credentials = config.username + ':' + config.password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}
    #####################################################

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    base_year = today_date[:4]

    # post_date = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    month     = calendar.month_name[int(opp_date[5:7])]
    day       = str(int(opp_date[8:]))
    num      = config.num_opps_per_fi # this is the number of opportunities to return - 
    num      = num + 2 # add a couple because found out some opportunities aren't trading anymore so we can have enought to find top 10 just in case

    top10_opp_df = {}

    for id in financial_groups.keys():

        # if id !=1 : continue # debugging
    
        years, pyears = assign_years_pyears(id)
        

        print('generating opp list for '+financial_groups[id])
        # dfs is for getting the company name to create the slugs
        symbols_csv = config.available_resources_path[str(id)]
        dfs = pd.read_csv(symbols_csv)[['symbols','name']]

        df=get_opp_list_for_display(num,id,month,day,years,pyears,appserver_token)
   
        # filter out opportunities in df that the last_trade_date is more than 7 days old - they are no longer trading or we lost our data
        test_date = inc_date_day(opp_date, -7) # use this date to filter out securities that are not trading 10/5/2023
        df = df[df['last_trade_date']>test_date]


        top10_opp_df[id]=df

        companies,slugs,bar_imgs,cum_imgs,sea_imgs,post_titles=get_slugs_for_opps(id,df,dfs,years,base_year)

        df['post_title'] = post_titles
        df['company']    = companies
        df['opp_slug']   = slugs
        df['bar_img']    = bar_imgs
        df['cum_img']    = cum_imgs
        df['sea_img']    = sea_imgs

        top10_opp_df[id]=df

        k = config.available_resources[str(id)] # the keys in top10_urls is the financial group name

        png_path_m = config.img_folder+'img/top10/'+config.top10_urls[k]+today_date+'-m.png'
        png_path_d = config.img_folder+'img/top10/'+config.top10_urls[k]+today_date+'-d.png'

        df['top_10_list_png_m'] = png_path_m
        df['top_10_list_png_d'] = png_path_d

        # print(png_path_d)

    return top10_opp_df
        
#------------------------------------------------------------------------------------------------
# loads the data by generating it 
def load_top10(filename):

    dict_df = {}
    action = 'generate' # generate or load - load happens only if file exist and its date is today

    file_exists = os.path.isfile(filename)
    if file_exists:
        mod_time = os.path.getmtime(filename)
        mod_datetime = datetime.datetime.fromtimestamp(mod_time) # convert the timestamp to a datetime object
        today = datetime.date.today()
        if mod_datetime.date() == today: # don't need to generate - its been created and saved in filename
            action = 'load'

    print(action)

    if action == 'generate':
        file_exists = os.path.isfile(filename)
        if file_exists:
            os.remove(filename)
        today_date = datetime.datetime.now().strftime("%Y-%m-%d")
        dict_df=get_top10_info(today_date)
        for key, df in dict_df.items():
            k = f'id{key}'
            df.to_hdf(filename, key=k, mode='a')
    else:   # load
        with pd.HDFStore(filename) as hdf:
            keys=hdf.keys()

        for k in keys:
            df=pd.read_hdf(filename,k)
            dict_df[int(k[3:])] = df

    return action,dict_df
    
#------------------------------------------------------------------------------------------------
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    filename = config.today_top10_data


    action,dfd=load_top10 (filename)

    # print(dfd[6].iloc[0]['opp_slug'])
    # print(dfd[0].iloc[0]['top_10_list_png_m'])
    print(dfd[0].iloc)




