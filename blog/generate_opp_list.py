# changed the parameters of 
# run_opp_blog_generation(opp_date):
# added parameter html_table_only boolean
# this is used to only create an html table for each opportunities list

import requests
import pandas as pd
import os
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




# after logging in, the returned token is used to make other calls to the appserver
def login_appserver(keyprovider_token):
    url = config.appserver_url+'/login/28/3/4/5/'+keyprovider_token
    api_result = requests.get(url)
    result = api_result.json()
    return result['token']




# this one decodes the token and return the list of financial groups from TradeWave
def get_financial_groups(appserver_token):
    data = jwt.decode(appserver_token, config.secret_key_appserver,algorithms=['HS256', 'RS256'])
    fgd={}
    for i in range(len(data['resource_disp'])):fgd[i]=data['resource_disp'][i]
    return fgd


#---------------------------------------------------------------------------------------------------------------

# get opportunities list for the financial group
def get_opp_list(group_id, month,day,years,pyears,appserver_token):
    
    urlX = config.appserver_url+'/OppList4/'+str(group_id)+'/'+month+'/'+day+'/'+str(years)+'/'+str(pyears)+'/-/0/0?token='+appserver_token
    api_result = requests.get(urlX)
    result = api_result.json()
    return result

#---------------------------------------------------------------------------------------------------------------


def get_chart_data(id,opp_date,symbol,daysOut,years,appserver_token):
    urlY = config.appserver_url+'/ChartData4/'+str(id)+'/'+opp_date+'/'+symbol+'/'+daysOut+'/'+str(years)+'?token='+appserver_token
    result = requests.get(urlY)
    if result.status_code > 201 : print('get_chart_data returned',result.status_code,result.text,result.reason)
    api_result = result.json()
    return api_result

#---------------------------------------------------------------------------------------------------------------

def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')  
#---------------------------------------------------------------------------------------------------------------


def diff_between_dates(date2,date1):

    d1 = datetime.datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.datetime.strptime(date2, "%Y-%m-%d")

    difference = d2 - d1
    days = difference.days
    return int(days)

#---------------------------------------------------------------------------------------------------------------

def get_opp_list_for_display(num_in_list,id,month,day,years,pyears,appserver_token):  # num_in_list = 10 would return top 10 opportunities

    opp_dict=get_opp_list(id,month,day,years,pyears,appserver_token)


    num_opps = len(opp_dict['OppList'])

    if num_opps > num_in_list: num_opps = num_in_list 
    top_opps=opp_dict['OppList'][:num_in_list] # top 10 list of lists
    df=pd.DataFrame(top_opps,columns=['Date','Symbol','DaysOut','Direction','Sharpe Ratio'])
    
    
    
    # add 5 columns to the top 10 list by calling et_chart_data for each opporutnity - same as clicking an opp in the dashboard
    cumulative = []
    avg_profit = []
    long_score = []
    short_score= []
    end_date   = []

    for i,r in df.iterrows():
        time.sleep(0.3)
        x=get_chart_data(id,r['Date'],r['Symbol'],r['DaysOut'],years,appserver_token)

    #     print(i,x['stats']['Cumulative Return'],x['stats']['Avg Profit'],x['stats']['Long Score'],x['stats']['Short Score'])
        cumulative.append(x['stats']['Cumulative Return'])
        avg_profit.append(x['stats']['Avg Profit'])
        long_score.append(x['stats']['Trend Long'])
        short_score.append(x['stats']['Trend Short'])

        end_date.append(inc_date_day(r['Date'],int(r['DaysOut'])))  # -1 is the correction - this is a problem *here

    df['Date2'] = end_date
    df['Cumulative Return'] = cumulative
    df['Avg Profit'] = avg_profit
    df['Trend Long'] = long_score
    df['Trend Short'] = short_score

    # column 'DaysOut' need to be incremented by 1 based on changes I made to the data a few months ago to match the opp list
    df['DaysOut'] = (df['DaysOut'].astype(int) + 1).astype(str)
        
    return df
#----------------------------------------------------------------------------------------------------------------------------

def create_html_table(df,dfs,domain_root,fi,years): # domain_root used for hrefs in <a> - dfs has the company names used for slug
    
    print('fi=',fi)

    titles = f"""

    <div style='height:20px;'></div>

    <details>
      <summary style='margin-bottom: 20px;'>
        <h2 class='report-h2-info'>
          Click an Opportunity for Detailed Report<span class='info-circle'><i>i</i></span>
        </h2>
      </summary>



<p style="background-color: lightgray; color:black;padding:5px;">
The top 10 seasonal opportunities for {fi} are discovered by TradeWave alogrithm by searching for the most seasonally active financial instruments on <span style='color:red;font-weight:bold'>{df['Date'].iloc[0]}</span>.  Each opportunity lists the information needed for trading the financial instrument:
</p>
<h3 class='report-h3'>Date1</h3>
<p style="background-color: lightgray; color:black;padding:5px;">
Date1 is the start date of the seasonal opportunity.  TradeWave provides top seasonal opportuniteis every day listed by the start-date.  date1 and start-date are equivalent in TradeWave.
</p>
<h3 class='report-h3'>Symbol</h3>
<p style="background-color: lightgray; color:black;padding:5px;">
Symbol is the unique ticker symbol that identifies the financial instrument. Each symbol corresponds to a specific financial instrument such as a stock, ETF, or futures contract.
</p>
<h3 class='report-h3'>Days</h3>
<p style="background-color: lightgray; color:black;padding:5px;">
Days and Days-held are the same in TradeWave.  It is important to understand, TradeWave defines date range as start-date and days-held. The end date for the date range is derived from start-date and days-held. This stat shows the number of days that the financial instrument should be held after the start day of date range opportunity. It helps investors determine the expected holding period for the trade and plan their investment strategy accordingly.
</p>
<h3 class='report-h3'>Date2</h3>
<p style="background-color: lightgray; color:black;padding:5px;">
Date2 or end date is the end date of the seasonal opportunity.  Date 2 is derived by adding Days to Date1.  
</p>
<h3 class='report-h3'>DIR</h3>
<p style="background-color: lightgray; color:black;padding:5px;">
Dir is the recommended trade direction for the financial instrument. It can either be long (buy) or short (sell). The recommendation is based on analysis of the financial instrument’s performance over the date range opportunity. The trade direction is one of the most important key stats to consider, as it indicates the expected price movement for the financial instrument in the coming days or weeks.
</p>
<h3 class='report-h3'>Sharpe Ratio</h3>
<p style="background-color: lightgray; color:black;padding:5px;">
The Sharpe ratio is a key statistic that provides a measure of risk-adjusted return. This stat takes into account both the profitability of the trading strategy and the level of risk associated with the strategy. A higher Sharpe ratio is generally considered to be a good indication of a more successful trading strategy, as it indicates that the returns generated are greater than the level of risk taken. However, it is important to note that a high Sharpe ratio does not necessarily guarantee a profitable trading strategy, as it is just one of several key stats that should be considered. When analyzing seasonal opportunities with large number of historical years, Sharpe Ratio value will be likely lower.
</p>
<h3 class='report-h3'>Cumulative</h3>
<p style="background-color: lightgray; color:black;padding:5px;">
the trading years (10 year by default) and provides an overall measure of the profitability of the trading strategy. A higher cumulative return is generally considered to be a good indication of a profitable trading strategy.
</p>
<h3 class='report-h3'>Avg Profit</h3>
<p style="background-color: lightgray; color:black;padding:5px;">
The average profit is a key statistic that provides an important overview of the profitability of the financial instrument. This stat is calculated by dividing the total profit from all trades during the date range opportunity by the total number of profitable trades. A higher average profit is generally considered to be a good indication of a profitable trading strategy, as it suggests that more winning trades were made than losing trades. However, it is important to note that this stat should be considered in conjunction with other key stats to fully understand the performance of the financial instrument. The best opportunities have a high average profit and a high Sharpe Ratio.
</p>
<h3 class='report-h3'>Trend Long</h3>
<p style="background-color: lightgray; color:black;padding:5px;">
Trend Long is a current uptrend score for the financial instrument. It is based on current data and provides a snapshot of the instrument’s recent behavior over the past 7 to 14 days. A higher Trend Long indicates that the financial instrument has been trending upward recently, and a lower score indicates the opposite. This score can provide valuable insight into the current momentum of the instrument and can be useful in making informed decisions about whether to buy or hold the instrument.
</p>
<h3 class='report-h3'>Trend Short</h3>
<p style="background-color: lightgray; color:black;padding:5px;">
This stat measures the current downtrend score for the financial instrument. Like the Trend Long, it is based on current data and provides a snapshot of the instrument’s recent behavior over the past 7 to 14 days. In most cases, the sum of Trend Long and Trend Short will add up to 100% most of the time. When Trend Short is higher than Trend Long, it may indicate a downward trend, while a higher Trend Long may indicate an upward trend.
</p>

    </details>

    <div class='blog-content-desktop'>

    <table class='opp-table-blog-desktop'>
      <tr class='no-highlight' style='font-weight:bold;color:black;border-bottom: 4px solid black;'>
        <td>Date1</td>
        <td>Date2</td>
        <td>Days</td>
        <td>Symbol</td>
        <td>DIR</td>
        <td>Sharpe Ratio</td>
        <td>Cumulative</td>
        <td>Avg Profit</td>
        <td>Trend Long</td>
        <td>Trend Short</td>
      </tr>
    """


    rows = ""
    for i,r in df.iterrows():

        # create the URL for the report for the anchors
        symbol = r['Symbol']
        dfx=dfs[dfs['symbols'] == symbol]
        company = ''
        # if dfx.shape[0] == 1: company = dfx['name'].iloc[0].lower().replace("'","").replace(' ','-').replace('-&-','-').replace('/','-')
        if dfx.shape[0] == 1: 
            company = dfx['name'].iloc[0]

        post_title = f"{years}Y Seasonal Report {company} ({symbol}) {r['Date']} to {r['Date2']}"
        post_slug = slugify(post_title)

        # report_url= f'{config.domain_root}seasonal-pattern-{company}-{symbol.lower()}-'+r['Date']
        report_url = f'{config.domain_root}{post_slug}'

        print('report_url=',report_url) 

        row = f"""

          <tr style='font-weight:normal' >
            <td><a href='{report_url}'>{r['Date']}</a></td>
            <td><a href='{report_url}'>{r['Date2']}</a></td>
            <td><a href='{report_url}'>{r['DaysOut']}</a></td>
            <td><a href='{report_url}'>{r['Symbol']}</a></td>
            <td><a href='{report_url}'>{r['Direction']}</a></td>
            <td><a href='{report_url}'>{r['Sharpe Ratio']}</a></td>
            <td><a href='{report_url}'>{r['Cumulative Return']}</a></td>
            <td><a href='{report_url}'>{r['Avg Profit']}</a></td>
            <td><a href='{report_url}'>{r['Trend Long']}</a></td>
            <td><a href='{report_url}'>{r['Trend Short']}</a></td>
          </tr>

        """
        rows = rows + row


    html_table = titles + rows + '</table></div>'

    ##################################################################
    show_mobile_table = True # this should be false only for debuging
    ##################################################################
    if show_mobile_table:

        # now write the table for mobile by reducing the columns to first 6
        html_table = html_table + """

        <div class = 'blog-content-mobile'>
        <table class='opp-table-blog-mobile'  >
          <tr class='no-highlight' style='font-weight:bold;color:black;border-bottom: 4px solid black;'>
            <td>Date1</td>
            <td>Symbol</td>
            <td>Days</td>
            <td>DIR</td>
            <td>Sharpe Ratio</td>
          </tr>
        """
        rows = ""
        for i,r in df.iterrows():
            symbol = r['Symbol']
            dfx=dfs[dfs['symbols'] == symbol]
            company = ''
            if dfx.shape[0] == 1: 
                company = dfx['name'].iloc[0]  #.lower().replace("'","").replace(' ','-').replace('-&-','-').replace('/','-')
            

          

        #   report_url= f'{config.domain_root}seasonal-pattern-{company}-{symbol.lower()}-'+r['Date']
            post_title = f"{years}Y Seasonal Report {company} ({symbol}) {r['Date']} to {r['Date2']}"
            post_slug = slugify(post_title)
            report_url = f'{config.domain_root}{post_slug}'


            row = f"""

                <tr style='font-weight:normal' >
                <td><a href='{report_url}'>{r['Date']}</a></td>
                <td><a href='{report_url}'>{r['Symbol']}</a></td>
                <td><a href='{report_url}'>{r['DaysOut']}</a></td>
                <td><a href='{report_url}'>{r['Direction']}</a></td>
                <td><a href='{report_url}'>{r['Sharpe Ratio']}</a></td>
                </tr>

            """

            rows = rows + row


        html_table = html_table + rows + '</table></div>'

        html_table = html_table + f"""

              <p style='font-size:1rem;'>
                  <a target='_blank' href='{config.domain_root}seasonal-analytics-101'>Seasonal Analytics 101</a>
                  <a target='_blank' href='{config.domain_root}top10'>Top 10 Today</a>
                  <a href = '{config.domain_root}wave-viewer/'>Discover Seasonal Trading Opportunities with Seasonal Scanner</a>
              </p>

        """

    
    return html_table
    
#--------------------------------------------------------------------------------------------------------------------------

def create_top_opps_blog_post(post_title,html_table,opp_date,num):
 
    post_slug = slugify(post_title)

    post = {
     'title'          : post_title,
     'status'         : 'publish', 
     'content'        : html_table,
     'categories'     : config.category_opp_top10, 
     'date'           : f'{opp_date}T05:00:00',
     'comment_status' : 'closed',
     'ping_status'    : 'closed',
     'slug'           : post_slug
     
    }

    username=config.username
    password=config.password
    credentials = username + ':' + password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}

    wait_for_php() # waits for php to have at least 2 idle processes  

    response = requests.post(config.post_endpoint_url , headers=header, json=post)
    print(response, response.reason)

#--------------------------------------------------------------------------------------------------------------------------
def update_page_custom_fields(post_id,custom_fields):

    # url = config.page_endpoint_url+'{}'.format(post_id)
    url = config.page_endpoint_url+'test'
    
    print('url=',url)

    payload = {
        'fields': custom_fields
    }

    username=config.username
    password=config.password
    credentials = username + ':' + password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}

    wait_for_php() # waits for php to have at least 2 idle processes  

    response = requests.post(url , headers=header, json=payload)
    print('updating custom fields: ',response, response.reason)

#----------------------------------------------------------------------------        
def get_all_post_titles_and_ids(header):
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

#------------------------------------------------------------------------------------------------r4f4
# def find_second_occurrence(string, search_string):
#     # find the index of the first occurrence of the search string
#     first_index = string.find(search_string)

#     # if the first occurrence was not found, return -1
#     if first_index == -1:
#         return -1

#     # find the index of the second occurrence of the search string
#     second_index = string.find(search_string, first_index + 1)

#     # if the second occurrence was not found, return -1
#     if second_index == -1:
#         return -1

#     return second_index
def find_nth_occurrence(string, substring, occurrence_number):
    start = string.find(substring)
    while start >= 0 and occurrence_number > 1:
        start = string.find(substring, start+len(substring))
        occurrence_number -= 1
    return start
#------------------------------------------------------------------------------------------------
#  return years and pyears based on which resource group from 0 or 11 is selected
#------------------------------------------------------------------------------------------------
def get_years_pyears_from_resource_id(id):
    years    = 10  
    pyears   = 10
    # if id == 0 or id == 5 or id == 11  or id==7 : 
    #     pyears = 9 # not enough results when returning 
    # if id == 9: 
    #     pyears = 8 # forex liquid
    # if id == 10  :  # this is bonds 
    #     years = 8
    #     pyears = 8
    return years,pyears
#------------------------------------------------------------------------------------------------
# run the opp blog generate
# html_tables_only is an option parameter used to create html tables used for home page and social media postings
def run_opp_blog_generation(id,opp_date,html_tables_only=False):

    
    ################ variables ########################
    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)
    credentials = config.username + ':' + config.password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}
    #####################################################

    month     = calendar.month_name[int(opp_date[5:7])]
    day       = str(int(opp_date[8:]))
    num      = config.num_opps_per_fi # this is the number of opportunities to return - 

    custom_field_dict = {}


    # if id !=0 : continue # debugging


    years    = 10  
    pyears   = 10
    if id == 0 or id == 5 or id == 11  or id==7 : pyears = 9 # not enough results when returning 
    if id == 9               : pyears = 8 # forex liquid
    if id == 10              :  # this is bonds 
        years = 8
        pyears = 8

    # print('years  pyears',id,years,pyears)


    print('generating opp list for '+config.available_resources[str(id)])

    # dfs is for getting the company name to create the slugs
    symbols_csv = config.available_resources_path[str(id)]
    dfs = pd.read_csv(symbols_csv)[['symbols','name']]

    # use names from here: config.available_resources_path
    html_file_name = os.path.splitext(os.path.basename(symbols_csv))[0].replace('_symbols','.html')
    html_file_path = config.chart_root_folder+'top10/'+html_file_name

    post_title = f'Top {num} Seasonal Patterns {config.available_resources[str(id)]}  {opp_date}'
    x=search_posts_by_title(post_title)

    #############################################################
    #############################################################
    # x=0 # remove after debugging - forces generation everytime
    #############################################################
    #############################################################


    if x > 0 : # this title already exists - skip creation
        return 'skipped'


    df=get_opp_list_for_display(num,id,month,day,years,pyears,appserver_token)



    print('config.available_resources[id]=',config.available_resources[str(id)])
    
    html_table=create_html_table(df,dfs,config.domain_root,config.available_resources[str(id)],years) # dfs has the company names
    
    # create an html table version only by stripping everything else out and save it to /var/www/html/wp-content/uploads/p/top10
    # start_index = html_table.find("<table")  # find the first occurrence of <table>
    # end_index = html_table.rfind("</table>")  # find the last occurrence of </table>
    start_index =  find_nth_occurrence(html_table, '<div',2)
    end_index   =  find_nth_occurrence(html_table, '</div>',3)

    html_table2 = html_table[start_index:end_index+len("</div>")] # everything except the tables and div are stripped
    # change the child div class name
    html_table2 = html_table2.replace('blog-content-desktop','top10-table-desktop').replace('blog-content-mobile','top10-table-mobile')
    

    # print(html_table2)
    
    # save html_table2 to html_file_path - this is snipits used to display in home page and twitter and facebook posts 4/14/2023
    with open(html_file_path,'w') as file: file.write(html_table2)


    custom_field_name = 'top10_table_'+html_file_name[:-5]
    custom_field_dict[custom_field_name]=html_table2 # adding html table to a dictionary for passing to wordpress to update custom fields
    

    create_top_opps_blog_post(post_title,html_table,opp_date,num)
    print('created for id=',id)
    return 'created'


#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    #---------------------------------------------
    # generate all opp blogs for today
    #---------------------------------------------
    # for id in config.available_resources:
    #     # if id != '9':continue
    #     run_opp_blog_generation(int(id),today_date)
    #---------------------------------------------
    # generate dow 30 opp blogs for today
    #---------------------------------------------
    # run_opp_blog_generation(0,today_date)
    #---------------------------------------------

    #---------------------------------------------
    # generate year long opp list
    #---------------------------------------------
    opp_date = today_date

    num_opps  = config.num_opps_per_fi # this is the number of opportunities to return - 
    month     = calendar.month_name[int(opp_date[5:7])]
    day       = str(int(opp_date[8:]))

    id       = 0
    years,pyears = get_years_pyears_from_resource_id(0)

    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)
    # df=get_opp_list_for_display(num_opps,id,month,day,years,pyears,appserver_token)


    result = get_opp_list(id,month,day,years,pyears,appserver_token)

    print(result['OppList'])