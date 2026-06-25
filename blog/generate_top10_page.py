
# 2/9/2023
# this script generates a top 10 page containing each of the 12 financial groups
# it needs to be updated each day with the links to the top 10 opportunites page for each of the 12 finaical groups


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
sys.path.insert(0, '/home/flask')
import config
from blog_tools import wait_for_php, search_posts_by_title, top10_by_market_title ,top10_by_sr_title
from slugify import slugify

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
  
    
    fgd = config.available_resources
  
    # data = jwt.decode(appserver_token, config.secret_key_appserver,algorithms=['HS256', 'RS256'])
    # fgd={}
    # for i in range(len(data['resource_disp'])):fgd[i]=data['resource_disp'][i]

    # print(fgd)

    return fgd




# get opportunities list for the financial group
def get_opp_list(group_id, month,day,years,pyears,appserver_token):
    
    urlX = appserver_url+'/OppList4/'+str(group_id)+'/'+month+'/'+day+'/'+str(years)+'/'+str(pyears)+'/-/0/0?token='+appserver_token
    api_result = requests.get(urlX)
    result = api_result.json()
    return result




def get_chart_data(id,opp_date,symbol,daysOut,years,appserver_token):
    urlY = appserver_url+'/ChartData4/'+str(id)+'/'+opp_date+'/'+symbol+'/'+daysOut+'/'+str(years)+'?token='+appserver_token
    result = requests.get(urlY)
    if result.status_code > 201 : print('get_chart_data returned',result.status_code,result.text,result.reason)
    api_result = result.json()
    return api_result



def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')  



def diff_between_dates(date2,date1):

    d1 = datetime.datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.datetime.strptime(date2, "%Y-%m-%d")

    difference = d2 - d1
    days = difference.days
    return int(days)






def top10_link(id,domain_root,fg,opp_date):
  
  num = 10

  id = str(id)  # 6/9/2023

  post_title = f'Top {num} TradeWave Opportunities {fg[id]}  {opp_date}'
  post_slug  = slugify(post_title)

  # make default - visual 6/15/2023
  post_slug = post_slug+'-t'

  return domain_root+post_slug

def create_html_table(domain_root,fg,opp_date): # domain_root used for hrefs in <a> - dfs has the company names used for slug
    

    title_top10_by_sr,slug_top10_by_sr,post_id=top10_by_sr_title(opp_date)



    html_table = f"""


  
    <p>&nbsp;</p>

     <details>
      <summary>
        <h2 class='report-h2-info'>
          Select a Financial Group <span class='info-circle'><i>i</i></span>
        </h2>
      </summary>

<br>
<p>&nbsp;</p>
      <p style="background-color: lightgray; color:black;padding:5px;">
TradeWave algorithm helps traders identify the most active repeating pattern financial instruments every day. The algorithm enables traders to access up to 10 TradeWave opportunities for each of the 12 financial groups.
</p>
<p style="background-color: lightgray; color:black;padding:5px;">
The five US stock financial groups are based on the top indexes: Dow 30 Stocks, Nasdaq 100 Stocks, S&amp;P500 Stocks, Russell 1000 Stocks, and Wilshire 5000 Stocks. When you click on a financial group, the list of opportunities is based on the stocks in that particular group. For instance, the Nasdaq 100 group lists the top 10 opportunities based on only the 100 stocks that are part of the Nasdaq 100. It is important to note that a stock may belong to more than one financial group and may appear in the top 10 opportunities list more than once.
</p>
<p style="background-color: lightgray; color:black;padding:5px;">
ETFs (Exchange Traded Funds) are investment funds traded on stock exchanges. The ETFs group in TradeWave comprises the top 200 ETFs with the highest volume as of 2022. Lower-volume ETFs were not considered for this list. Traders can use the TradeWave opportunities identified in this group to make informed investment decisions.
</p>
<p style="background-color: lightgray; color:black;padding:5px;">
The futures and commodities group is based on over 200 instruments available in TradeWave. Futures contracts are agreements to buy or sell an underlying asset at a predetermined price and date. Commodities are basic goods that are used in commerce, such as agricultural products, energy, and metals. The TradeWave opportunities identified in this group can help traders make predictions about future market trends.
</p>
<p style="background-color: lightgray; color:black;padding:5px;">
Indices are portfolios of stocks that represent a specific market or industry. The Indices common group in TradeWave comprises a list of the most commonly traded indices in worldwide markets and in US markets. Traders can use this list to access the most popular indices and make informed investment decisions. The Indices group lists more than 200 available indices that span worldwide markets. Traders can explore this list for the best TradeWave opportunities for specific indices.
</p>
<p style="background-color: lightgray; color:black;padding:5px;">
Forex (Foreign Exchange) is the largest financial market in the world, with an estimated daily turnover of more than $6 trillion. The Forex All group in TradeWave lists TradeWave patterns based on 900+ forex pairs. Forex pairs are combinations of two currencies that are traded against each other. Traders can use this group to access the most active repeating patterns in forex pairs and make informed investment decisions.
</p>
<p style="background-color: lightgray; color:black;padding:5px;">
Forex Liquid is another TradeWave group that lists TradeWave opportunities based on the 18 highest volume forex pairs. The high volume of these pairs means that they are among the most actively traded pairs in the forex market. Traders can use the opportunities identified in this group to gain insights into the behavior of the most liquid forex pairs.
</p>
<p style="background-color: lightgray; color:black;padding:5px;">
Government bonds are debt securities issued by governments to finance their spending. These bonds are considered low-risk investments, as governments are generally seen as reliable borrowers. The Government Bonds group in TradeWave lists the most active repeating opportunities based on 100+ government bonds traded worldwide. Traders can use this group to access the most active repeating patterns in government bonds and make informed investment decisions.
</p>
<p style="background-color: lightgray; color:black;padding:5px;">
By exploring the TradeWave opportunities identified in each financial group, traders can gain insights into the behavior of specific markets and make informed investment decisions. TradeWave provides a valuable tool for traders looking to identify the most active repeating patterns in financial instruments every day.
</p>

 </details>
    
    <div style='height:20px;'>
    </div>
    


    <div class='buttons-top-div'>
      
      <div class='top-left-column' >
        <a href='{top10_link(0,domain_root,fg,opp_date)}'><button class='button-top'>DOW 30 STOCKS</button></a>
        <a href='{top10_link(1,domain_root,fg,opp_date)}'><button class='button-top'>NASDAQ 100 STOCKS</button></a>
        <a href='{top10_link(2,domain_root,fg,opp_date)}'><button class='button-top'>S&P 500 STOCKS</button></a>
        <a href='{top10_link(3,domain_root,fg,opp_date)}'><button class='button-top'>RUSSELL 1000 STOCKS</button></a>
        <a href='{top10_link(4,domain_root,fg,opp_date)}'><button class='button-top'>WILSHIRE 5000 STOCKS</button></a>
        <a href='{top10_link(11,domain_root,fg,opp_date)}'><button class='button-top'>ETFS</button></a>
      </div>

      <div class='top-right-column'>
        <a href='{top10_link(7,domain_root,fg,opp_date)}'><button class='button-top'>FUTURES & COMMODITIES</button></a>
        <a href='{top10_link(5,domain_root,fg,opp_date)}'><button class='button-top'>INDICES COMMON</button></a>
        <a href='{top10_link(6,domain_root,fg,opp_date)}'><button class='button-top'>INDICES ALL</button></a>
        <a href='{top10_link(8,domain_root,fg,opp_date)}'><button class='button-top'>FOREX ALL</button></a>
        <a href='{top10_link(9,domain_root,fg,opp_date)}'><button class='button-top'>FOREX LIQUID</button></a>
        <a href='{top10_link(10,domain_root,fg,opp_date)}'><button class='button-top'>GOVERNMENT BONDS</button></a>
      </div>

    </div>
    


    <p style='font-size:1rem;'>
    <a target='_blank' href='{domain_root}top10'>Top 10 Today</a>
    <a target='_blank' href='{domain_root}/{slug_top10_by_sr}'>Top 10 TradeWave Opportunities based on Sharpe Ratio on {opp_date}</a>
    <a target='_blank' href='{domain_root}/tradewave-analytics-101'>TradeWave Analytics 101</a>
    <a href = '{domain_root}tradewave-viewer/'>TradeWave Viewer: Discover Trading Opportunities for all Financial Instruments</a>
    <a target='_blank' href = '{domain_root}top10-archive/'>Top 10 Time Capsule</a>
    </p>


    """

    return html_table
    


def create_top_10_post(html_table,opp_date,financial_groups,header):
 


    post_date = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    title = top10_by_market_title(opp_date)
    title_by_sr = top10_by_sr_title(opp_date)

    # title     = f'Top 10 Opportunities for each market '+opp_date

    x=search_posts_by_title(title)
    top10_slug = slugify(title)
    if x > 0 : # this post this exist skip
        print(title,' creation skipped')
        return top10_slug

    

    # check if this title has been created 
    # all_posts_titles_ids_list=get_all_post_titles_and_ids()
    # all_posts_titles_list = [x['title'].replace('&amp;','&') for x in all_posts_titles_ids_list]
    
    # for item in all_posts_titles_ids_list:
    #     x_title = item['title'].replace('&amp;','&')
    #     x_id = item['id']
    #     x_slug = item['slug']
    #     if x_title == title:
    #         print(title,' creation skipped')
    #         return x_slug
    
    # if title in all_posts_titles_list: # it's been created already - has to be deleted before it can be created again
    #     print(title,' creation skipped')
    #     return 


    post = {
     'title'          : title,
     'slug'           : top10_slug,
     'status'         : 'publish', 
     'content'        : html_table,
     'categories'     : config.category_top10, 
     'date'           : post_date,
     'comment_status' : 'closed',
     'ping_status'    : 'closed',
     'meta':{
         'custom_header': f'<link rel="stylesheet" href="{config.domain_root}wp-content/themes/css/ts_top10.css">'
     }
    }

    response = requests.post(config.post_endpoint_url , headers=header, json=post)
    x=response.json()

    print('Top 10 page created -',response, response.reason)

    return x['generated_slug']
#----------------------------------------------------------------------------        


#-------------------------------------------------------------------------------------------------------------------
def get_redirect(url,redirect_endpoint_url,header): # return json for the source url 
     # this is the return id if there is a match or is -1
    ret_json = {}
    response = requests.get(redirect_endpoint_url+'redirect', headers=header)
    if response.status_code > 201:
        print('Get Redirects:',response.status_code,response.reason)
    else:
        redir = response.json()
        lst=redir['items']

        for r in lst:

            id=r['id']
            j_url=r['url']
            match_url=r['match_url']
            action_url=r['action_data']['url']
            if url == match_url or url == j_url:
                ret_json = r
                break
    print('Get Redirects:',response.status_code,response.reason)         
    return ret_json  
#-------------------------------------------------------------------------------------------------------------------
def create_new_redirect(url1,url2):
    if url1[-1] == '/':url1=url1[:-1]
        
    payload = {
        'source': url1,
        'url'   : url1+'/',
        'action_data': {'url': url2},
        'regex': False,
        'group_id': 1,
        'action_type': 'url',
        'match_type': 'url'
    }
    
    response = requests.post(config.redirect_endpoint_url+'redirect',json=payload ,headers=header)
    print('Create Redirect:',response.status_code,response.reason)
    redir = response.json() 
#-------------------------------------------------------------------------------------------------------------------
def update_redirect(ret_json,dest_url,redirect_endpoint_url,header):


    # print('rrrrrrrrrrrrrrrrrrrrr',ret_json['id'],':',dest_url)

    # update the json with the new dest url
    ret_json['action_data']['url']= dest_url
    url = redirect_endpoint_url+'redirect/'+str(ret_json['id'])
    # print(url)
    # exit()
    response = requests.post(url,json=ret_json, headers=header)
    print('Update Redirect:',response.status_code,response.reason)
#-------------------------------------------------------------------------------------------------------------------

#-------------------------------------------------------------------------------------------------------------------
def run_top10_page(opp_date):

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


    # financial_groups=get_financial_groups(appserver_token)


    domain_root   = config.domain_root 
    credentials = username + ':' + password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}
    redirect_endpoint_url = domain_root + 'wp-json/redirection/v1/'
    #####################################################


    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    # opp_date = today_date # opp_date by default is today - can override here

    keyprovider_token = get_keyprovider_token()
    appserver_token   = login_appserver(keyprovider_token)


    financial_groups  = get_financial_groups(appserver_token)


    # df=get_opp_list_for_display(num,id)
    html_table=create_html_table(domain_root,financial_groups,opp_date) # dfs has the company names
    slug=create_top_10_post(html_table,opp_date,financial_groups,header)

    print('slug=',slug)


    return # skipping redirect below because I created set_redirect.py and switch redirect to genreat_top10_sr.py

    # create top10 redirects only if opp-date is today
    if opp_date == today_date:
        # now create a redirect to point /top10 to this slug - this has to be done daily
        # get all the redirects to see if there is a redirect already from a previous day, otherwise create one
        rj = get_redirect('/top10',redirect_endpoint_url,header) # checks againts the url field
      



        if not bool(rj) : # dictionary is empty
            print('creating a new redirect')
            if slug[1:] != '/':slug='/'+slug
            create_new_redirect('/top10/',slug)
        else:
            print('updating existing redirect')
            print(rj)
            update_redirect(rj,slug,redirect_endpoint_url,header)
    else:
        print('skipped creating top10 redirect opp_date != today_date ',opp_date,today_date)

#-------------------------------------------------------------------------------------------------------------------

#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    run_top10_page(today_date)


    # keyprovider_token=get_keyprovider_token()
    # appserver_token=login_appserver(keyprovider_token)
    # get_financial_groups(appserver_token)