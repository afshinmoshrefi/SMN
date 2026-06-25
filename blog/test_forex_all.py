# this script creates a top10 page based on SR  8/15/2023

# line 297 needs to be commented out after this is done

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
from blog_tools import wait_for_php, search_posts_by_slug,get_company_name,assign_years_pyears,top10_by_sr_title, top10_by_market_title
from set_redirect import redirect_url # 8/16/2023
# from get_top10_based_on_sr import load_top10_based_on_sr
from slugify import slugify
from thumbnails import create_socialmedia_thumbnail
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
    # df=pd.DataFrame(top_opps,columns=['Date','Symbol','DaysOut','Direction','Sharpe Ratio'])
    # updated columns 8/10/2023
    df=pd.DataFrame(top_opps,columns=['Date','Symbol','DaysOut','Direction','Sharpe Ratio','avg_profit','median_profit','cumulative_return','stddev'])
    
    
    
    
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

def create_html_table(df,domain_root,resource_id,post_title,opp_date): # domain_root used for hrefs in <a> - dfs has the company names used for slug

    # fi = config.available_resources[str(resource_id)]


    title_top10_by_market = top10_by_market_title(opp_date)
    slug_top10_by_market = slugify(title_top10_by_market)
    link_top10_by_market = config.domain_root+slug_top10_by_market

    img_src = []
    img_alt = []
    report_url = []

    opportunity_num = []
    opp_market = []

    for i,r in df.iterrows():
        img_alt.append('')
        img_src.append(r['tn_url'])
        company = r['company']
        # generate link to report
        # company = get_company_name(resource_id,r['Symbol'])
        report_title = f"{r['years']}-Year TradeWave Report {company} ({r['Symbol']}) {r['Date']} to {r['Date2']}"
        report_slug = slugify(report_title)
        url = f'{config.domain_root}{report_slug}'
        report_url.append(url)
        opportunity_num.append(str(i+1))
        opp_market.append(r['market_name'])



    html_top = f"""
  
    <p>&nbsp;</p>

    <a href='#scroll-to-bottom' style='font-size:1.2em; font-weight:bold; display: inline-block; background-color: #59D93B; color: white; padding: 10px 20px; border-radius: 15px; text-decoration: none;'>Get The Top 10 Straight To Your Inbox</a>


    <p >
        Welcome to the TradeWave Top 10 page, where you'll find meticulously curated trading opportunities with the highest Sharpe Ratios across 12 diverse markets. Explore enticing thumbnails highlighting optimal purchase and selling dates, along with average returns over a decade. Click on a thumbnail for detailed insights, empowering informed decisions in your trading journey.
    </p>
    
    <div style='height:20px;'>  </div>

     <details>
      <summary style='margin-bottom: 20px;'>
        <h2 class='report-h2-info'>
          Click an Opportunity Thumbnail to view the Detailed Report<span class='info-circle'><i>i</i></span>
        </h2>
      </summary>





<p style="background-color: lightgray; color: black; padding: 5px;">
    Our comprehensive scans encompass 12 distinct markets, including the Dow 30, Nasdaq 100, S&amp;P 500, Russell 1000, Wilshire 5000, ETFs, Indices, Indices Common, Futures &amp; Commodities, Forex, Liquid Forex, and Government Bonds.
</p>

<p style="background-color: lightgray; color: black; padding: 5px;">
    Each listing on this page represents an opportunity that has demonstrated a remarkable <span style="font-weight: bold;">Sharpe Ratio</span>. These opportunities are elegantly displayed as engaging thumbnails, offering key insights such as the optimal purchase date, the recommended selling date, and the average historical return over the past 10 years. To enhance your experience, each thumbnail is accompanied by the symbol's market association and its rank within our esteemed top 10 list. For instance, the symbol ranked #3 represents the third highest Sharpe Ratio for today's list of opportunities, spanning all markets.
</p>

<p style="background-color: lightgray; color: black; padding: 5px;">
    If you're eager to explore a specific opportunity further, simply click on its thumbnail. This action will promptly lead you to a detailed report, equipping you with the knowledge needed to make well-informed trading decisions. To see Top 10 opportunities for each individual market, click the link below this description.
</p>

<p>&nbsp;</p>

    </details>
    
"""

    # print(report_url)
    # exit()

    # opportunity_num = []
    # opp_market = []

    # for i in range(0,10):
    #     if i%2 == 0: print(i)
    # for i in range(0,10):
    #     if i%2 != 0: print(i)


    col1 = ''
    col2 = ''
    for i in range(0,10):
        if i%2 == 0:
            col1+=f'<span style="font-size:1rem"><b> #{opportunity_num[i]} - {opp_market[i]}</b></span>'
            col1+= f"<a href='{report_url[i]}'><img src='{img_src[i]}' alt='{img_alt[i]}'></a>\n"
            # print('i=',i)

    for i in range(0,10):
        if i%2 != 0:
            col2+=f'<span style="font-size:1rem;"><b> #{opportunity_num[i]} - {opp_market[i]}</b></span>'
            col2+= f"<a href='{report_url[i]}'><img src='{img_src[i]}' alt='{img_alt[i]}'></a>\n"
            # print('i=',i)


    

    # html_content = f"""
    # <div class='blog-content-desktop' '>
      
    #   <div class='top-left-column'>
    #     {col1}
    #   </div>

    #   <div class='top-right-column'>
    #     {col2}
    #   </div>  
    # </div>
    
    # """

    html_content_desktop = f"""
    <div class='top10-div-desktop'>
      
      <div class='top-left-column'>
        {col1}
      </div>

      <div class='top-right-column'>
        {col2}
      </div>  
    </div>
    
    """

    # create mobile content now
    col1 = ''
    for i in range(0,10):
        col1+=f'<span style="font-size:1rem"><b> #{opportunity_num[i]} - {opp_market[i]}</b></span>'
        col1+= f"<a href='{report_url[i]}'><img src='{img_src[i]}' alt='{img_alt[i]}'></a>\n"

    html_content_mobile = f"""
    <div class='top10-div-mobile'>
      <div class='top-left-column'>
        {col1}
      </div>
    </div>
    
    """





    html_bottom = f"""
    <p style='font-size:1rem;'>
    <a target='_blank' href='{link_top10_by_market}'>Top 10 for each Market on {opp_date}</a>
    <a target='_blank' href='{domain_root}/tradewave-analytics-101'>TradeWave Analytics 101</a>
    <a href = '{domain_root}wave-viewer/'>Wave Viewer: Discover Trading Opportunities for all Financial Instruments</a>
    <a target='_blank' href = '{domain_root}top10-archive/'>Top 10 Time Capsule</a>
    </p>

    <p>
     <span style='font-size:0.7rem'>*Thumbnail images are for illustrative purposes only. They do not imply endorsements or affiliations. Please refer to our <a href='/terms-conditions'>Terms of Service</a> for more information.</span>
    </p>

    <div id="scroll-to-bottom"></div>
    
    """


    html_table = html_top + html_content_desktop + html_content_mobile + html_bottom

    return html_table
    
#--------------------------------------------------------------------------------------------------------------------------

def create_top_opps_blog_post(post_title,html_table,opp_date):
 
    post_slug = slugify(post_title) # _t is for having a new slug for opp_list blog with thumbnails

    post = {
     'title'          : post_title,
     'status'         : 'publish', 
     'content'        : html_table,
     'categories'     : config.category_sr_tn, 
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

    return post_slug

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
# adding links to the reports here also - its redundant but helps generate_emails_sr
#------------------------------------------------------------------------------------------------
def generate_thumbnails(df): # resource_id is int


    urls=[]
    paths=[]
    report_url=[]

    title_pre = '' # some cases like fb posts, I put title_pre = 'Profit Alert: '

    # print(df)
    # exit()


    for i,r in df.iterrows():
        path,url=create_socialmedia_thumbnail('tn',r['resource_id'],r['Date'],r['Symbol'],r['DaysOut'],r['Direction'],r['Avg Profit'],r['years'],title_pre,config.category_report)
        urls.append(url)
        paths.append(path)
        

        report_title = f"{r['years']}-Year TradeWave Report {r['company']} ({r['Symbol']}) {r['Date']} to {r['Date2']}"
        report_slug = slugify(report_title)
        r_url = f'{config.domain_root}{report_slug}'
        report_url.append(r_url)


        print(i,'done')

    df['tn_url']=urls
    df['path']=paths
    df['report_url']=report_url


    return df
#------------------------------------------------------------------------------------------------
# run the opp blog generate for top10 based on sr
# html_tables_only is an option parameter used to create html tables used for home page and social media postings
def run_opp_blog_generation_thumbnails_sr(df,opp_date,post_title,post_slug):

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    
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

    # years,pyears = assign_years_pyears(id)

    custom_field_dict = {}

    # dfs is for getting the company name to create the slugs
    # dfs = pd.read_csv(symbols_csv)[['symbols','name']]

    

    # post_title = f'Top 10 TradeWave Opportunities based on Sharpe Ratio on {opp_date}'
    # post_slug = slugify(post_title)


    x=search_posts_by_slug(post_slug) #_t is for thumbnails to distinguish post with the list posts

    #############################################################
    #############################################################
    # x=0 # remove after debugging - forces generation everytime
    #############################################################
    #############################################################
    print('x=',x)
    if x > 0 : # this title already exists - skip creation
        return 'skipped'



    # use the thumbnails to create a opp blog page with images
    df=generate_thumbnails(df)


    # generate content    
    html_table=create_html_table(df,config.domain_root,id,post_title,opp_date) # dfs has the company names
    
 

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


    

    post_slug=create_top_opps_blog_post(post_title,html_table,opp_date) # -2 is for removing _t added for thumnails
  
    print(post_slug)

    #------------------------------------------------------------------------------------
    # if opp_date is todayredirect top10 link on the main menu to the newly created page 
    #------------------------------------------------------------------------------------
    if opp_date == today_date:
        redirect_url('/top10',post_slug)
        print('redirect /top10 to ',post_slug)


    return df
#-------------------------------------------------------------------------------------------------------------------
# this creates the post_title and searches if it exists, if exists, return post_id, otherwise -1
# this also gnerates the post_title for the top 10 opportunities for each market used for link from sr to top10 by market


# def generate_post_title_sr_search(opp_date):

#     post_title = f'Top 10 TradeWave Opportunities based on Sharpe Ratio on {opp_date}'
#     post_slug = slugify(post_title)
#     post_id=search_posts_by_slug(post_slug) 

#     # post_title_top10_by_markets = 

#     return post_title,post_slug,post_id
#-------------------------------------------------------------------------------------------------------------------
def load_top10_based_on_sr(opp_date):

    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)


    list_df = []
    for key in config.available_resources:
        id = int(key)
        print('processing ',id)
        
        if (id == 8):  # stopping Forex All from top 10
            continue

        years,pyears = assign_years_pyears(id)

        print('years=',years,config.available_resources[key])

        month     = calendar.month_name[int(opp_date[5:7])]
        day       = str(int(opp_date[8:]))
        num      = config.num_opps_per_fi # this is the number of opportunities to return - 

        # restrict government bonds if this setting is 0 or a positive number
        if 'BONDS' in config.available_resources[key] and config.restrict_gov_bonds_sr > -1: 
            num = config.restrict_gov_bonds_sr

        df_by_id=get_opp_list_for_display(num,id,month,day,years,pyears,appserver_token)

        df_by_id['resource_id'] = id
        df_by_id['market_name'] = config.available_resources[key]
        df_by_id['years'] = years
        # --------- get company names ---------------------------

        symbols_csv = config.available_resources_path[key]
        dfs = pd.read_csv(symbols_csv)[['symbols','name']]
        company_list = []
        for i,r in df_by_id.iterrows():
            symbol = r['Symbol']
            dfc=dfs[dfs['symbols'] == symbol]
        
            company = ''
            if dfc.shape[0] == 1: company = dfc['name'].iloc[0]
            company_list.append(company)

        df_by_id['company'] = company_list




        list_df.append(df_by_id)


    df = pd.concat(list_df,ignore_index=True)
    df = df.sort_values(by='Sharpe Ratio',ascending=False)

    # df rows on top have a lot of duplicates. we need top10 without duplicates
    df = df.drop_duplicates(subset='Symbol').reset_index(drop=True) # chatgpt showed me how to do it in 1 line

    df = df[df['avg_profit']>config.top10_avg_profit_filter]  # drop opportunities less then 5% average profit - setting is in config

    df = df[:10].reset_index(drop=True)

    return df

#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    opp_date = today_date


    df=load_top10_based_on_sr(opp_date)
    print(df)
    exit()


    # opp_date = '2023-03-01'

    print('processing top10 for ',opp_date)


    # check if this exists, otherwise skip it
    title_top10_by_sr,slug_top10_by_sr,post_id=top10_by_sr_title(opp_date)

    print(opp_date)
    print(post_id)

    exit()

    print(slug_top10_by_sr)
    # post_id=-1 # uncomment to force to recreate
    if post_id == -1: # -1 is not found
        df = load_top10_based_on_sr(opp_date)
        # df.to_csv('df.csv')
        # df = pd.read_csv('df.csv')
        dfx=run_opp_blog_generation_thumbnails_sr(df,opp_date,title_top10_by_sr,slug_top10_by_sr)

    # df = load_top10_based_on_sr(opp_date)
    # print(df)