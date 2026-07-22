
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



def get_opp_list_for_display(num_in_list,id):  # num_in_list = 10 would return top 10 opportunities
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
        time.sleep(0.1)
        x=get_chart_data(id,r['Date'],r['Symbol'],r['DaysOut'],years,appserver_token)

    #     print(i,x['stats']['Cumulative Return'],x['stats']['Avg Profit'],x['stats']['Long Score'],x['stats']['Short Score'])
        cumulative.append(x['stats']['Cumulative Return'])
        avg_profit.append(x['stats']['Avg Profit'])
        long_score.append(x['stats']['Trend Long'])
        short_score.append(x['stats']['Trend Short'])
        end_date.append(inc_date_day(r['Date'],int(r['DaysOut'])))

    df['Date2'] = end_date
    df['Cumulative Return'] = cumulative
    df['Avg Profit'] = avg_profit
    df['Trend Long'] = long_score
    df['Trend Short'] = short_score

    # column 'DaysOut' need to be incremented by 1 based on changes I made to the data a few months ago to match the opp list
    df['DaysOut'] = (df['DaysOut'].astype(int) + 1).astype(str)
    
    
    
    
    return df


def top10_link(id,domain_root):
  x=financial_groups[id].lower().replace(' ','-').replace('&','')
  x= domain_root+'top-10-seasonal-patterns-'+x+'-'+opp_date

  return x

def create_html_table(domain_root,all_top10_posts): # domain_root used for hrefs in <a> - dfs has the company names used for slug
    

    html_table = f"""


    <div style='height:30px;'> </div>
    

     <details>
      <summary>
        <h2 class='report-h2-info'>
          Access archived top 10 lists by date <span class='info-circle'><i>i</i></span>
        </h2>
      </summary>

    <br>
    <p>&nbsp;</p>
        
        
        <p style="background-color: lightgray; color:black;padding:5px;">
        Welcome to the Trade Seasonal Report Archive page! Here you will find a comprehensive collection of our Top 10 lists from previous reports. Our top 10 lists provide valuable insights and analysis for seasonal trading opportunities that traders can capitalize on.
        </p>
        <p style="background-color: lightgray; color:black;padding:5px;">
        If you're new to Trade Seasonal, our Top 10 lists are accessible from the top menu on the day of the report. However, after the current day, the older Top 10 lists get archived and can be accessed by navigating to this archive page. Please note that these archives will only be accessible for a limited time.
        </p>
        <p style="background-color: lightgray; color:black;padding:5px;">
        Our Top 10 lists cover a variety of markets and asset groups, including equities, futures, Indices, Bonds, and forex. We use a data-driven approach to analyze seasonal patterns, historical data, and statistical indicators to identify the top trades with the highest probability of success. 
        </p>
        <p style="background-color: lightgray; color:black;padding:5px;">
        To access the archived Top 10 lists, simply navigate to the archive page and select the date you are interested in. The lists are sorted by date, so you can easily find the report you're looking for. 
        </p>
        <p style="background-color: lightgray; color:black;padding:5px;">
        Whether you're a novice trader or an experienced professional, our Top 10 lists provide valuable insights and ideas that can help you make informed trading decisions. Our reports are easy to understand and provide a clear explanation of the market conditions, trends, and opportunities.
        </p>
        <p style="background-color: lightgray; color:black;padding:5px;">
        In addition to the Top 10 lists, we also provide a variety of other resources to help you stay up-to-date on the latest market trends and trading strategies. 
        </p>
        <p style="background-color: lightgray; color:black;padding:5px;">
        Thank you for choosing Trade Seasonal as your trusted source for seasonal trading analysis. We hope that our Top 10 lists and other resources help you achieve your investment and trading goals and succeed in the markets.
        </p>



    </details>
        
        <div style='height:20px;'></div>
    
    
    """

    html_table = html_table + "<div class='archive-div'>"

# add the archives here

    for post in all_top10_posts:
        
        html_table = html_table + f"""

       <a href = {domain_root}/{post['slug']}>  <button class='archive-button'>  {post['title']} </button>  </a> 

        """


    html_table = html_table + "</div>"

    html_table = html_table + f"""

        <a target='_blank' href='{domain_root}/tradewave-analytics-101'>TradeWave Analytics 101</a>
        <a href = '{domain_root}app/'>Wave Viewer: Discover TradeWave Opportunities for all Financial Instruments</a>
        <a target='_blank' href='{domain_root}top10'>Top 10 Today</a>
        
    """

    return html_table
    


def create_archive(html_table,title,slug,header):
 


    post_date = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    

    

    post = {
     'title'          : title,
     'slug'           : slug,
     'status'         : 'publish', 
     'content'        : html_table,
     'categories'     : config.category_top10_archive, 
     'date'           : post_date,
     'comment_status' : 'closed',
     'ping_status'    : 'closed',
     
    }

    response = requests.post(config.post_endpoint_url , headers=header, json=post)
    x=response.json()

    print('Top 10 archive page created -',response, response.reason)

    return x['generated_slug']
#----------------------------------------------------------------------------        
def get_all_top10_posts(header):
    page = 1
    all_posts = []



    # url = config.post_endpoint_url + "?categories="+str(config.category_top10)+"&per_page=100&_fields=title,id,slug&page={}"
    url = config.post_endpoint_url + "?categories="+str(config.category_sr_tn)+"&per_page=100&_fields=title,id,slug&page={}"



    while True:
        response = requests.get(url.format(page), headers=header)
        if response.status_code == 200:
            posts = response.json()
            for post in posts:
                all_posts.append({
                    "title": post["title"]["rendered"],
                    "id": post["id"],
                    "slug":post["slug"]
                })
            if len(posts) < 100:
                break
            page += 1
        else:
            break

    return all_posts        

#-------------------------------------------------------------------------------------------------------------------
def get_redirect(url): # return json for the source url 
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
    
    response = requests.post(redirect_endpoint_url+'redirect',json=payload ,headers=header)
    print('Create Redirect:',response.status_code,response.reason)
    redir = response.json() 
#-------------------------------------------------------------------------------------------------------------------
def update_redirect(ret_json,dest_url):
    # update the json with the new dest url
    ret_json['action_data']['url']= dest_url
    response = requests.post(redirect_endpoint_url+'redirect/'+str(ret_json['id']),json=ret_json, headers=header)
    print('Update Redirect:',response.status_code,response.reason)
#-------------------------------------------------------------------------------------------------------------------

#-------------------------------------------------------------------------------------------------------------------
def run_creating_archive():
    ################ variables ########################
    appserver_url = config.appserver_url
    post_endpoint_url = config.post_endpoint_url
    # staging blog username set as author
    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)
    financial_groups=get_financial_groups(appserver_token)
    credentials = config.username + ':' + config.password
    post_token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}
    redirect_endpoint_url = config.domain_root + 'wp-json/redirection/v1/'
    #####################################################


    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    opp_date = today_date # opp_date by default is today - can override here

    month    = calendar.month_name[int(opp_date[5:7])]
    day      = str(int(opp_date[8:]))


    keyprovider_token = get_keyprovider_token()
    appserver_token   = login_appserver(keyprovider_token)
    financial_groups  = get_financial_groups(appserver_token)

    all_top10_posts = get_all_top10_posts(header)

    for post in all_top10_posts:
        post['date']=post['title'][-10:]

    # sort and make the newest on top
    all_top10_posts = sorted(all_top10_posts, key=lambda x: x['date'], reverse=True)

    html_table=create_html_table(config.domain_root,all_top10_posts)

    slug  = 'top10-archive'
    title = 'Top 10 TradeWave Opportunity Archive'
    # delete the old archive before creating a new one
    response = requests.get(post_endpoint_url+'?categories='+str(config.category_top10_archive))
    res=response.json()
    if len(res) > 1: # should not have more than 1 archive - its a problem if its more than 1
        print('search returned',len(res),'results - it should have returned 1 - delete 1 of them and run again')
        exit()
    if len(res) == 1: # delete this archive before creating a new one
        post_id = res[0]['id']
        
        response = requests.delete(post_endpoint_url+str(post_id), headers=header)
        print(response.status_code)
        if response.status_code == 200:
                print(title+" deleted")

        else:
            print(p['title']['rendered']+"Request failed with status code:", response.status_code)

    # create new archive
    slug = create_archive(html_table,title,slug,header)
    print(slug)


#-------------------------------------------------------------------------------------------------------------------

#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':
    run_creating_archive()


