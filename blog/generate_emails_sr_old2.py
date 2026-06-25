# this python script creates and sends daily emails based on the user preference
# it uses the thumbnails type "tn" 
# this is the type created for the top_to_opp_list_w_thumbnails - all thumbnails are in the /tn subfolder
# use the tracking json to find the path to the thumbnails to generate the emails

# width: calc(70% - 100px); /* Calculate the width based on the sizes of other divs */


import pandas as pd
import datetime
from datetime import timedelta
import mailerlite as MailerLite
import sys
import requests
import json
import pprint
from get_top10_data import load_top10,get_chart_data
from blog_tools import json_log, convert_param_base64,top10_link,get_keys
sys.path.insert(0, '/home/flask')
import config
import redis
from generate_top10_sr import load_top10_based_on_sr,generate_thumbnails
import os,os.path

redis_client  = redis.Redis(host='localhost', port=6379, db=0)  # used as a cache
redis_client2 = redis.Redis(host=config.appserver_ip, port=6379, db=2)  # used as a db

mailerlite_token = config.mailerlite_token

#-----------------------------------------------------------------------------------------------------
def create_subscriber(email,first_name,last_name,ip,optin_ip):
    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.create(email, fields={'name': first_name, 'last_name': last_name}, ip_address=ip, optin_ip=optin_ip)
    return response
#-----------------------------------------------------------------------------------------------------
def get_all_subscribers():
    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.list(limit=10, page=1, filter={'status': 'active'})
    return response
#-----------------------------------------------------------------------------------------------------
def today_date_hour_min():
    dt=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(dt)

    d = dt[:10]
    h = dt[11:13]
    m = dt[14:16]

    return d,h,m

#-----------------------------------------------------------------------------------------------------
def future_date_hour_min(num_minutes):
    dt=datetime.datetime.now()
    future_datetime = dt + timedelta(minutes=num_minutes)
    fdate = future_datetime.strftime("%y-%m-%d")
    
    # Format the time as '%H:%M:%S'
    ftime = future_datetime.strftime("%H:%M:%S")

    d = fdate[:10]
    h = ftime[:2]
    m = ftime[3:5]

    return d,h,m




#-----------------------------------------------------------------------------------------------------


# this function loads all the data for sending emails to users
# it selects the top opportunity only unless its a duplicate.  then
# it selects the next opportunity until it finds a unique one
# row_pos contains the number of opportunity selected, typically 0
def load_data_for_email():

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    cur_year   = today_date[:4]
    json_filename = f'{config.thumbnails_json_file}{cur_year}/tn_tracking-{today_date}.json'

    print('json_filename=',json_filename)

    # load thumbnail json file to get the thumbnails generated when top10 was created 
    dict_list=json_log(json_filename,'get',{})
    df = pd.DataFrame(dict_list)

    # I did this because I screwed up the filename - had to use a bigger file 
    # df = df[df['date1']>'2023-06-30'] # remove 7/21/2023

    filename = config.today_top10_data

    # dfd is a dictionary of dataframes with the keys for each of the 12 financial groups
    action,dfd=load_top10 (filename) # this generates/loads the information for all top10 for 12 markets.  its an hdf file

    

    resource_id = []
    top_rows    = []  # this is the row we pick for the resource_id
    row_pos     = []  # typically 0 for each resource_id unless its a duplicate symbol then go to next row

    # create a new dataframe with the first item from each of 12 resource_id - add resource_id as a column
    symbols = []  # use this to stop duplicate symbols specially for stocks
    for i in range(len(dfd)):
        for j in range (0,7): # we want the 1st item but if its a duplicate - check the next until unique
            row = dfd[i].iloc[j]
            symbol = row['Symbol']
            if symbol in symbols:
                continue
            else:
                symbols.append(symbol)
            break
        row_pos.append(j)
        top_rows.append(row)
        resource_id.append(i)

    dfe = pd.DataFrame(top_rows).reset_index(drop=True)
    dfe['resource_id']=resource_id
    dfe['row_pos']=row_pos

    # putting resource_id at the begining because it looks better
    # row_pos is typically 0 for the first one - if its a duplicate of previous resource_id, goes to next until unique
    dfe = dfe[['resource_id','row_pos','Symbol','Date', 'DaysOut', 'Direction', 'Sharpe Ratio', 'Date2','Cumulative Return', 'Avg Profit', 'Trend Long', 'Trend Short','post_title', 'company', 'opp_slug']]
    #################################################################
    #      dfe is now a list of the 12 opportunities for email      #
    #################################################################

    # find thumbnails for the 12 opportunities based on the top10 thumbnails that
    # were generated earlier today 

    tn = []  # search for thumbnails in df and put it in dfe
    fn = []  # saving filename just in case 
    load_urls = []
    top10_page_urls = []

 

    for i,r in dfe.iterrows():
        date1=r['Date']
        symbol=r['Symbol']
        days=r['DaysOut']
        resource_id=r['resource_id']
        t = r['post_title'].split('-')
        years = t[0] # years was missing so I'm getting it from post_title

        print('date1,symbol,days,resource_id,years=',date1,symbol,days,resource_id,years)


        dft = df[ (df['date1']==date1) & (df['symbol']==symbol) & (df['days']==days) & (df['resource_id']==resource_id) ]
        filename = dft['filename'].iloc[0]
        tn_url   = dft['url'].iloc[0]

        print(tn_url)
        
        sv_param      = convert_param_base64(resource_id,symbol,date1,days,years)
        load_url       = config.domain_root+'tradewave-viewer?o='+sv_param
        top10_page_url = top10_link(resource_id)


        load_urls.append(load_url)
        top10_page_urls.append(top10_page_url)

        fn.append(filename)
        tn.append(tn_url)

    dfe['tn_file']  = fn
    dfe['tn_url']   = tn
    dfe['load_url'] = load_urls
    dfe['top10_page_url']=top10_page_urls


    return dfe
#-----------------------------------------------------------------------------------------------------




#-----------------------------------------------------------------------------------------------------
def get_users_from_redis():
    # query for all the keys that hold user_email_settings
    _,ump_key = get_keys()  # gets from keyprovider. 1st is interval keys and second is UMP key in WordPress

    keys = redis_client2.keys('user_email_settings_*')
    # print('keys in redis = ',keys)

    users_to_email = []
    if len(keys) == 0 :
        return 0
    # create a dataframe of all the users to email 
    for k in keys:
        kd = k.decode() # convert byte to string
        user_bytes=redis_client2.get(kd)

        if user_bytes is not None:
            user_dict = json.loads(user_bytes)
            users_to_email.append(user_dict)

    df = pd.DataFrame(users_to_email)
    
    # make sure all the emails are in the mailerlite
    # also update flags in case user changed it 

    return df
#-----------------------------------------------------------------------------------------------------

#-----------------------------------------------------------------------------------------------------
def create_mailerlite_group(group_name):
    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.groups.create(group_name)
    return response
#-----------------------------------------------------------------------------------------------------
def update_mailerlite(df):
    
    for i,r in df.iterrows():
        first_name=r['first_name']
        last_name=r['last_name']
        email=r['email']
        flags=r['flags']
        r=create_subscriber(email,first_name,last_name,'','')
        print(r)
        print('')

#---------------------------------------------------------------------------------------------------
# purpose is to create an easy name -> group_id  dictionary for use in other functions
#-----------------------------------------------------------------------------------------------------
def get_email_groups():
    client = MailerLite.Client({'api_key': config.mailerlite_token })
    response = client.groups.list(limit=100, page=1, sort='name')

    dict_groups = {} # all the other groups

    for g in response['data']:
       
            name = g['name'] # strip tt_ - the name has to be tt_ in mailerlite to seperate from other groups
            g_id = g['id']
            dict_groups[name]=g_id


    return dict_groups
#---------------------------------------------------------------------------------------------------
def assign_subscriber_to_a_group(subscriber_id,group_id):

    client = MailerLite.Client({'api_key': config.mailerlite_token })
    response = client.subscribers.assign_subscriber_to_group(int(subscriber_id), int(group_id))

    print('assign to group response = ',response)
#---------------------------------------------------------------------------------------------------
def unassign_subscriber_from_a_group(subscriber_id,group_id):

    client = MailerLite.Client({'api_key': config.mailerlite_token })
    response = client.subscribers.unassign_subscriber_from_group(int(subscriber_id), int(group_id))

    print('unassign from group response = ',response)

#-----------------------------------------------------------------------------------------------------
#-----------------------------------------------------------------------------------------------------
def create_campaign(campaign_name,subject,from_name,from_email,group_id,content):

    client = MailerLite.Client({'api_key': mailerlite_token })

    params = {
        "name": campaign_name,
        "language_id": 4,
        "type": "regular",
        "emails": [{
                "subject": subject,
                "from_name": from_name,
                "from": from_email,
                "content": content
            }],
        "groups":[
            group_id
        ]
    }

    response = client.campaigns.create(params)
    # pprint.pprint(response)
    

    campaign_id   = int(response['data']['id'])
    campaign_time = response['data']['created_at']
    return campaign_id,campaign_time
#-----------------------------------------------------------------------------------------------------
def schedule_campaign(campaign_id,send_date,send_hour,send_minute):
    client = MailerLite.Client({'api_key': mailerlite_token })

    params = {
        "delivery": "scheduled",
        "schedule": {
            "date": send_date,
            "hours": send_hour,
            "minutes": send_minute
        }
    }

    response = client.campaigns.schedule(campaign_id, params)
    return response
#-----------------------------------------------------------------------------------------------------
def content_row_html(content1,content2,cols=2):
    
    content_row = '<tr><td align="center"><table cellpadding="0" cellspacing="0" border="0">'

    if cols == 2:
        content_row += f""" 
                <tr style='background-color:transparent'>
                    <td align="center" style="padding: 10px;">
                        {content1}
                    </td>
                    <td align="center" style="padding: 10px;">
                        {content2}
                    </td>
                </tr>
            """
    elif cols == 1:
        content_row += f""" 
                <tr >
                    <td align="center" style="padding: 10px">
                        {content1}
                    </td>
                </tr>
            """
                

    content_row += '</table></td></tr>'
              
    return content_row
#-----------------------------------------------------------------------------------------------------
# this is where each email cell is generated with header, content, foolter
# for new email remove the footer and replace header with a simple description text without rect
#-----------------------------------------------------------------------------------------------------
def content_cell_html(dict):

    num_fg_color   = dict['num_fg_color']
    num_bg_color   = dict['num_bg_color']
    title_color    = dict['title_color']
    footer_color   = dict['footer_color']
    footer_h       = dict['footer_h']
    img_src        = dict['img_src']
    img_height     = dict['img_height']
    img_width      = dict['img_width']
    img_alt        = dict['img_alt']
    title_text     = dict['title_text']
    title_num      = dict['title_num']
    report_link    = dict['report_link']
    load_link      = dict['load_link']
    top10_text     = dict['top10_text']
    top10_link     = dict['top10_link']
    link_color     = dict['link_color']
    link_font_size = dict['link_font_size']

    content_cell = f"""


                <table cellpadding="0" cellspacing="0" border="0" >
                    <tr>
                        <td style='vertical-align: middle;text-align: center'>
                            <!-- start title -->
                               <b> #{title_num} - {title_text} </b>
                            <!-- end title -->
                        </td>
                    </tr>

                    <tr>    
                        <td>    
                            <a href="{report_link}" target="_blank" rel="noopener noreferrer">
                                <img src="{img_src}" alt="{img_alt}" width="{img_width}" height="{img_height}" style="display: block; border: 0; margin: 0; padding: 0;">
                            </a>
                        </td>
                    </tr>        
  
                </table>
    
    """
    return content_cell
#-----------------------------------------------------------------------------------------------------
def create_all_content_cells(dfe):

    title_color       = 'rgb(211,211,211)'
    img_color         = 'black'
    link_color        = 'violet'
    num_bg_color      = 'black'
    num_fg_color      = 'white'
    footer_font_color = 'white'
    footer_color      = 'lightblue'

    link_color        = 'black' 
    link_font_size    = '1rem'


    img_width = '350'
    img_height= '194'

    padding = '0.4vw'
    margin  = '10px'
    title_h = '65px'
    title_w = '35px'
    title_c = '329px'
    footer_h = '30px'
    center_div_gap = '20px' # seperation of right and left divs
    title_font_size = '1vw'


    dict = {}
    dict['num_fg_color']   = num_fg_color
    dict['num_bg_color']   = num_bg_color
    dict['title_color']    = title_color
    dict['footer_color']   = footer_color
    dict['footer_h']       = footer_h
    dict['img_height']     = img_height
    dict['img_width']      = img_width
    dict['link_color']     = link_color
    dict['link_font_size'] = link_font_size

    cell_dict = {}

    for i,r in dfe.iterrows():
        
        resource_text        = config.available_resources[str(r['resource_id'])]
        tt_link_text         = config.available_resources[str(r['resource_id'])].replace('STOCKS',' ').replace('GOVERNMENT BONDS','GOV BONDS').replace('FUTURES & COMMODITIES','FUTURES & COMM')
        
        if tt_link_text != 'ETF': tt_link_text = tt_link_text.title()

        # if resource_text == 'INDICES': resource_text += ' ALL'
        # if resource_text != 'FUTURES & COMMODITIES':resource_text += ' Top 10'
        # tt_link_text  += ' Top 10'


        dict['img_src']      = r['tn_url']
        dict['img_alt']      = ''
        dict['title_text']   = resource_text
        dict['title_num']    = str(r['row_pos'])
        dict['report_link']  = r['report_url']
        dict['load_link']    = r['load_url']
        dict['top10_text']   = tt_link_text
        dict['top10_link']   = r['top10_page_url']
  
    
        content_cell = content_cell_html(dict)

        cell_dict[i] = content_cell

    return cell_dict
#-----------------------------------------------------------------------------------------------------
def create_email_html_desktop(cells_dict,flags):

    # print(cells_dict.keys())


    # exit()

    content_row1 = content_row_html(cells_dict[0],cells_dict[1],2) # can be 1 or 2 
    content_row2 = content_row_html(cells_dict[2],cells_dict[3],2) # can be 1 or 2 
    content_row3 = content_row_html(cells_dict[4],cells_dict[5],2) # can be 1 or 2 
    content_row4 = content_row_html(cells_dict[6],cells_dict[7],2) # can be 1 or 2 
    content_row5 = content_row_html(cells_dict[8],cells_dict[9],2) # can be 1 or 2 

    # content filtered is based on flags 111111111111   111110000001    000001111110  000000000000
    content_filtered = ''

    content_filtered = f'''
        {content_row1}
        {content_row2}
        {content_row3}
        {content_row4}
        {content_row5}
    '''
   


 

    return content_filtered
#-----------------------------------------------------------------------------------------------------
def media_query_style_html():
    mq = """
        <style>
        @media screen and (min-width:600px){
            .desktop-version {display:table;}
            .mobile-version {display:none;}
        }
        @media screen and (max-width:599px){
            .desktop-version {display:none;}
            .mobile-version {display:table;}
        }



        </style>
    """
    return mq
#-----------------------------------------------------------------------------------------------------
def create_email_html_smartphone(cells_dict,flags):

    content_row1  = content_row_html(cells_dict[0],{},1) # can be 1 or 2 
    content_row2  = content_row_html(cells_dict[1],{},1) 
    content_row3  = content_row_html(cells_dict[2],{},1) 
    content_row4  = content_row_html(cells_dict[3],{},1) 
    content_row5  = content_row_html(cells_dict[4],{},1) 
    content_row6  = content_row_html(cells_dict[5],{},1) 
    content_row7  = content_row_html(cells_dict[6],{},1) 
    content_row8  = content_row_html(cells_dict[7],{},1) 
    content_row9  = content_row_html(cells_dict[8],{},1) 
    content_row10 = content_row_html(cells_dict[9],{},1) 

     # content filtered is based on flags 111111111111   111110000001    000001111110  000000000000

    content_filtered = f'''
        {content_row1}
        {content_row2}
        {content_row3}
        {content_row4}
        {content_row5}
        {content_row6}
        {content_row7}
        {content_row8}
        {content_row9}
        {content_row10}
    '''





    return content_filtered
#-----------------------------------------------------------------------------------------------------
def content_footer(desktop_or_smartphone):
    
    content_row = '<tr  ><td align="center" ><table cellpadding="0" cellspacing="0" border="0" style="background-color:lightgray" >'

    content_row += f""" 
                <tr >
                    <td width="720px" height="100px" align="center" style=background-color:lightgray;padding: 10px; border-bottom:1px solid black;margin:10px;">
                       <a href='{config.domain_root}wave-viewer?set=on' style='font-size:1.5rem'> Update your email preferences or unsubscribe
                    </td>
                </tr>
            """
                

    content_row += '</table></td></tr>'
              
    return content_row
    
#-----------------------------------------------------------------------------------------------------
def content_header(desktop_or_smartphone,flags):

    header_td_width = '720px'
    if desktop_or_smartphone == 'smartphone':
        header_td_width = '360'
    
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

   

    content_row = '<tr  ><td align="center" ><table cellpadding="0" cellspacing="0" border="0" style="background-color:transparent" >'

    content_row += f""" 
                <tr >

                    <td  height="100px" width="40px" align="left" style='background-color:transparent;padding-top:3px'> 
                        <img src = {config.logo_png} width='70' height='70'>
                    </td>

                    <td width="520px" height="100px" align="left" style='vertical-align:middle;background-color:transparent'> 
                        
                    
                            <div >Daily Top 10 Email </div>
                            <div >Wave Analytics International</div>
                            <div ><a style='color:black;font-weight:bold;font-size:1.1rem' href='https://tradewave.ai'>TradeWave.AI</a></div>
                      

                    </td>
                </tr>

                <tr>
                    <td  colspan="2" width='{header_td_width}' height="50px" align="left" style='background-color:transparent;padding-left:5px;padding-top:10px;border-top:1px solid black'>
                        <span><br> Welcome to the TradeWave Daily Email for {today_date}: Unveiling the Top 10 Waves! Within this email, you'll embark on a journey through a curated collection of meticulously analyzed trading Opportunities – or as we call them, Waves. <br><br>These Waves emerge from the sea of data analytics, surfacing with pinpoint accuracy on the dates boasting the highest Sharpe ratios. Spanning a decade of historical data across 12 diverse markets, each Wave offers a strategic entry and exit point expertly condensed into concise previews. Furthermore, you'll gain access to a decade's worth of average returns, painting a comprehensive portrait for your discerning evaluation. <br><br>If you're ready to explore the depths, a simple click unveils an ocean of insight into any of the top 10 Wave opportunities. This detailed report equips you with the robust statistics and dynamic charts necessary to navigate the trading currents and make astute decisions for today's market tides.<br><br><br> </span>
                    </td>
                </tr>

                
               




            """
                
    content_row += '</table></td></tr>'
              
    return content_row
#-----------------------------------------------------------------------------------------------------
def create_final_email(dfe):

    flag = '111111111111'

    cells_dict         = create_all_content_cells(dfe)                  # create cell content for each of the 12 Top Opportunities
    content_desktop    = create_email_html_desktop(cells_dict,flag)    # create html content for the desktop version
    content_smartphone = create_email_html_smartphone(cells_dict,flag) # create html content for the smartphone version
    media_query_html   = media_query_style_html()                       # using media queries for switch between desktop & mobile

    # there is a conditional script below to eliminate desktop table from outlook
    # outlook doesn't support media queries so we'll just send mobile version to 
    # both desktop and mobile outlook clients
    # that's because mobile version look ok on desktop but desktop version doesn't 
    # look good on mobile

    # create the final content for the email

    content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Email-Friendly Images</title>
            {media_query_html}

        </head>
        <body>

                <!-- add content_desktop only if its not outlook -->
                <!--[if !mso]><!-->
                <table class="desktop-version" width="100%" cellpadding="0" cellspacing="0" border="0" style="table-layout: fixed;background-color:transparent">
                    {content_header('desktop',flag)}
                    {content_desktop}
                    {content_footer('desktop')}
                </table>
                <!--<![endif]-->


                <table class="mobile-version" width="100%" cellpadding="0" cellspacing="0" border="0" style="table-layout: fixed;background-color:transparent">
                    {content_header('smartphone',flag)}
                    {content_smartphone}
                    {content_footer('smartphone')}
                </table>


        </body>
        </html>
    """

  
    with open("/var/www/html/wp-content/test.html", "w") as file:  file.write(content)
    

    return content

#-----------------------------------------------------------------------------------------------------    
def get_num_subscribers(group_id):

    client = MailerLite.Client({'api_key': config.mailerlite_token })
    response = client.groups.get_group_subscribers(group_id, page=1, limit=10, filter={'status': 'active'})

    subscribers = response['data']
    return len(subscribers)

    
#-----------------------------------------------------------------------------------------------------
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    # dfe = load_data_for_email()

    # dict_email_groups= get_email_groups() # these are list of all groups in mailerlite

    # print(dict_email_groups)

    # print(dfe)
    # print(dfe.columns)

    # dfe.to_csv('dfe.csv')

    # # email_content=create_final_email(dfe,flag)

    action = 'generate'
    filename = config.today_top10_sr
    file_exists = os.path.isfile(filename)
    
    # file_exists = False

    if file_exists:
        mod_time = os.path.getmtime(filename)
        mod_datetime = datetime.datetime.fromtimestamp(mod_time) # convert the timestamp to a datetime object
        today = datetime.date.today()
        if mod_datetime.date() == today: # don't need to generate - its been created and saved in filename
            action = 'load'

    if action == 'generate':
        if file_exists: os.remove (filename)
        df = load_top10_based_on_sr(today_date)
        df = generate_thumbnails(df)
        # df = generate_report_links(df)  # adds url to the report for each thumbnail to be clicked
        df.to_csv(filename)
    else :
        df = pd.read_csv(filename)

    df['load_url']=''
    df['top10_page_url']=''
    df['row_pos'] = range(1,df.shape[0]+1)
    print(df.columns)

    email_content=create_final_email(df)

    group_name_to_send_daily_email = 'daily-top10-email-opt-in'

    dict_groups = get_email_groups()
    # send daily top10 emails to users with the following group_id
    group_id = dict_groups[group_name_to_send_daily_email]
    print('send emails to group_id:',group_id)

    num_subscribers = get_num_subscribers(int(group_id))

    print('send emails to',num_subscribers,'subscribers')
   



    if num_subscribers > 0:
        campaign_name = f'TW Top10 Daily Email for {today_date}'
        
        subject       = f"📈 TradeWave Top10 Securities | Date: {today_date}"

        from_name     = 'TradeWave Top 10 Daily'
        from_email    = 'admin@tradeseasonals.com'
    
        # sending to subscribers of group_id
        campaign_id,campaign_time=create_campaign(campaign_name,subject,from_name,from_email,group_id,email_content)
        d,h,m = future_date_hour_min(1) # get time 1 minute from now


        schedule_campaign(campaign_id,d,h,m)




    
   
      


    






