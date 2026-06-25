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
from blog_tools import wait_for_php, search_posts_by_slug,get_company_name,assign_years_pyears
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

def create_html_table(df,dfs,domain_root,resource_id,years,post_title): # domain_root used for hrefs in <a> - dfs has the company names used for slug

    fi = config.available_resources[str(resource_id)]

    img_src = []
    img_alt = []
    report_url = []
    for i,r in df.iterrows():
        img_alt.append('')
        img_src.append(r['url'])
        # generate link to report
        company = get_company_name(resource_id,r['Symbol'])
        report_title = f"{years}-Year TradeWave Report {company} ({r['Symbol']}) {r['Date']} to {r['Date2']}"
        report_slug = slugify(report_title)
        url = f'{config.domain_root}{report_slug}'
        report_url.append(url)

    # creating the link for when the visual/detail switch is clicked
    opp_list_link = slugify(post_title) # _t is for having a new slug for opp_list blog with thumbnails   
    opp_list_link = config.domain_root+opp_list_link


    html_top = f"""
  
    <p>&nbsp;</p>

   <div style='width:10rem'> <a href='{opp_list_link}'><img src='{config.domain_root}/wp-content/uploads/2024/01/visual.png'></a></div>
    
    <div style='height:20px;'>  </div>

     <details>
      <summary style='margin-bottom: 20px;'>
        <h2 class='report-h2-info'>
          Click an Opportunity Thumbnail to view the Detailed Report<span class='info-circle'><i>i</i></span>
        </h2>
      </summary>



<p style="background-color: lightgray; color:black;padding:5px;">Welcome to the TradeWave Top 10 page for {fi}.  The opportunities on this page are for {df['Date'].iloc[0]}; your ultimate resource for discovering the top 10 trading opportunities! This page showcases the most promising opportunities with enticing thumbnails, highlighting important details such as the date to buy, date to sell, and average return. If you want to delve deeper into a specific opportunity, simply click on its thumbnail, and you'll be directed to a detailed report.</p>

<p style="background-color: lightgray; color:black;padding:5px;">On the thumbnails page, you'll notice a convenient toggle switch positioned at the top left. This switch allows you to seamlessly switch between two views: visual and Detail. By default, the visual view presents the top 10 opportunities with eye-catching thumbnails. It's a quick and visually appealing way to scan through the potential trades. However, if you crave more in-depth information, simply toggle the switch to the Detail view.</p>

<p style="background-color: lightgray; color:black;padding:5px;">In the Detail view, you'll find a comprehensive table with 10 rows, providing a wealth of valuable data for each opportunity. To access the full details of any specific opportunity, simply click on the corresponding row in the table. This will take you to a detailed report, where you can explore the opportunity from every angle.</p>

<p style="background-color: lightgray; color:black;padding:5px;">Our aim is to make your trading experience as smooth and insightful as possible.</p>
<p style="background-color: lightgray; color:black;padding:5px;">Embrace the power of informed decision-making with our TradeWave Reports. Happy trading!</p>

<p>&nbsp;</p>

    </details>
    
"""

    # print(report_url)
    # exit()

   

    col1 = ''
    if len(report_url)>=1:col1+= f"<a href='{report_url[0]}'><img src='{img_src[0]}' alt='{img_alt[0]}'></a>\n"
    if len(report_url)>=2:col1+= f"<a href='{report_url[1]}'><img src='{img_src[1]}' alt='{img_alt[1]}'></a>\n"
    if len(report_url)>=3:col1+= f"<a href='{report_url[2]}'><img src='{img_src[2]}' alt='{img_alt[2]}'></a>\n"
    if len(report_url)>=4:col1+= f"<a href='{report_url[3]}'><img src='{img_src[3]}' alt='{img_alt[3]}'></a>\n"
    if len(report_url)>=5:col1+= f"<a href='{report_url[4]}'><img src='{img_src[4]}' alt='{img_alt[4]}'></a>\n"

    col2 = ''
    if len(report_url)>=6:col2+= f"<a href='{report_url[5]}'><img src='{img_src[5]}' alt='{img_alt[5]}'></a>\n"
    if len(report_url)>=7:col2+= f"<a href='{report_url[6]}'><img src='{img_src[6]}' alt='{img_alt[6]}'></a>\n"
    if len(report_url)>=8:col2+= f"<a href='{report_url[7]}'><img src='{img_src[7]}' alt='{img_alt[7]}'></a>\n"
    if len(report_url)>=9:col2+= f"<a href='{report_url[8]}'><img src='{img_src[8]}' alt='{img_alt[8]}'></a>\n"
    if len(report_url)>=10:col2+= f"<a href='{report_url[9]}'><img src='{img_src[9]}' alt='{img_alt[9]}'></a>\n"


    html_content = f"""
    <div class='buttons-top-div'>
      
      <div class='top-left-column'>
        {col1}
      </div>

      <div class='top-right-column'>
        {col2}
      </div>  
    </div>
    
"""
    html_bottom = f"""
    <p style='font-size:1rem;'>
    <a target='_blank' href='{domain_root}top10'>Top 10 Today</a>
    <a target='_blank' href='{domain_root}/tradewave-analytics-101'>Seasonal Analytics 101</a>
    <a href = '{domain_root}tradewave-viewer/'>TradeWave Viewer: Discover Trading Opportunities for all Financial Instruments</a>
    <a target='_blank' href = '{domain_root}top10-archive/'>Top 10 Time Capsule</a>
    </p>

    <p>
     <span style='font-size:0.7rem'>*Thumbnail images are for illustrative purposes only. They do not imply endorsements or affiliations. Please refer to our <a href='/terms-conditions'>Terms of Service</a> for more information.</span>
    </p>

    """

    html_table = html_top + html_content + html_bottom

    return html_table
    
#--------------------------------------------------------------------------------------------------------------------------

def create_top_opps_blog_post(post_title,html_table,opp_date,num):
 
    post_slug = slugify(post_title+'_t') # _t is for having a new slug for opp_list blog with thumbnails

    post = {
     'title'          : post_title,
     'status'         : 'publish', 
     'content'        : html_table,
     'categories'     : config.category_opp_top10t, 
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

def generate_thumbnails(df,resource_id,years): # resource_id is int


    urls=[]
    paths=[]

    title_pre = '' # some cases like fb posts, I put title_pre = 'Profit Alert: '

    # print(df)
    # exit()


    for i,r in df.iterrows():
        path,url=create_socialmedia_thumbnail('tn',resource_id,r['Date'],r['Symbol'],r['DaysOut'],r['Direction'],r['Avg Profit'],years,title_pre,config.category_report)
        urls.append(url)
        paths.append(path)
        print(i,'done')
    df['url']=urls
    df['path']=paths
    return df
#------------------------------------------------------------------------------------------------
# run the opp blog generate
# html_tables_only is an option parameter used to create html tables used for home page and social media postings
def run_opp_blog_generation_thumbnails(id,opp_date,html_tables_only=False):

    
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


    years,pyears = assign_years_pyears(id)

    # print('years  pyears',id,years,pyears)


    print('generating opp list for '+config.available_resources[str(id)])

    # dfs is for getting the company name to create the slugs
    symbols_csv = config.available_resources_path[str(id)]
    dfs = pd.read_csv(symbols_csv)[['symbols','name']]

    # use names from here: config.available_resources_path
    html_file_name = os.path.splitext(os.path.basename(symbols_csv))[0].replace('_symbols','.html')
    html_file_path = config.chart_root_folder+'top10/'+html_file_name

    post_title = f'Top {num} TradeWave Opportunities {config.available_resources[str(id)]}  {opp_date}'
    post_slug = slugify(post_title+'_t')


    x=search_posts_by_slug(post_slug) #_t is for thumbnails to distinguish post with the list posts

    #############################################################
    #############################################################
    # x=0 # remove after debugging - forces generation everytime
    #############################################################
    #############################################################

    if x > 0 : # this title already exists - skip creation
        return 'skipped'


    df=get_opp_list_for_display(num,id,month,day,years,pyears,appserver_token)

 

    # use the thumbnails to create a opp blog page with images
    df=generate_thumbnails(df,id,years)



    print('config.available_resources[id]=',config.available_resources[str(id)])
    
    html_table=create_html_table(df,dfs,config.domain_root,id,years,post_title) # dfs has the company names
    
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
    

    create_top_opps_blog_post(post_title,html_table,opp_date,num) # -2 is for removing _t added for thumnails
    print('created for id=',id)
    return df


#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    df=run_opp_blog_generation_thumbnails(0,today_date)

    if type(df) == str:
        print(df)
        exit()

    for i,r in df.iterrows():
        print(r['url'])
        print(r['path'])
        print('')

    exit()
    #---------------------------------------------
    # generate all opp blogs for today
    #---------------------------------------------
    # for id in config.available_resources:
    #     # if id != '9':continue
    #     run_opp_blog_generation(int(id),today_date)
    #---------------------------------------------
    # generate dow 30 opp blogs for today
    #---------------------------------------------
    run_opp_blog_generation(4,today_date)
    #---------------------------------------------





