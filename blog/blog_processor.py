# blog processor is a service that reads the redis queue "queue" and 
# processes each item as 

# 1) seasonal report based on top 10 - auto generated 
# 2) Top 10 list for each of the financial groups - auto generated
# 3) Landing for Top 10 lists - 12 buttons with links to 2) - auto generated
# 4) Time Capsule (Archive) page with links to all pages created in 3) - auto generated
# 5) Date Range Report, similair to 1) but for any date range and any number of years - user initiated - notification
# 6) Delete Reports 
#    a) number of days old 
#    b) category
#    c) userid
#    d) invididual reports by title
#    e) delete by max total reports - if reach 1000,000 - the oldest reports are removed
# 7) Cleanup - checks all reports and images to make sure all images have a report.  if an image has no report, remove

# to check how many messages in queue use the following command in redis-cli: LLEN date_range_opp_queue

import requests
from flask import Flask,jsonify,request
import os
import datetime
import time
import redis
import json
from create_report import generate_report
import sys
from generate_opp_blogs import run_opp_blog_generation
from generate_opp_blogs_w_thumbnails import run_opp_blog_generation_thumbnails
from generate_top10_page import run_top10_page
from generate_archive import run_creating_archive 
from m_facebook import post_one_of_am_dr_reports,del_one_of_am_dr_reports

from generate_top10_sr import load_top10_based_on_sr,run_opp_blog_generation_thumbnails_sr,top10_by_sr_title,top10_by_market_title

import base64

sys.path.insert(0, '/home/flask')
import config

stream_name = 'date_range_opp_queue'

redis_client = redis.Redis(host='localhost', port=6379, db=0)

#--------------------------------------------------------------------------------
# process will pop each opp_dict from the queue and
# 1- create a report for the date range
# 2- add the report to the user list
# 3- add the free user reports to the free users running list
# 4- add to the logs on logcollector for the requesting user - logcollector keeps request datetime and creation datetime
# 5- send a notification email to the report initiator
#--------------------------------------------------------------------------------

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
def del_report(post_slug,post_id):

    credentials = config.username + ':' + config.password
    token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + token.decode('utf-8')}

    l = post_slug.split('-')
    date1      = f'{l[-7]}-{l[-6]}-{l[-5]}'
    base_year  = l[-7]
    subfolders = f'{base_year}/{date1}/' # image subfolders

    b_img = 'gain-loss-barchart-'+post_slug+'.png'
    c_img = 'cumulative-return-'+post_slug+'.png'
    s_img = 'trend-chart-'+post_slug+'.png'
    b_img = config.chart_root_folder+subfolders+b_img
    c_img = config.chart_root_folder+subfolders+c_img
    s_img = config.chart_root_folder+subfolders+s_img
    print('b_img=',b_img)
    # remove the 3 images
    if os.path.exists(b_img):os.remove(b_img)
    if os.path.exists(c_img):os.remove(c_img)
    if os.path.exists(s_img):os.remove(s_img)
    # remove image subfolder if its empty
    if os.path.isdir(config.chart_root_folder+subfolders) and not os.listdir(config.chart_root_folder+subfolders):
        os.rmdir(config.chart_root_folder+subfolders)
    # remove the post from wordpress now
    # url = f'{config.post_endpoint_url[:-1]}?slug={post_slug}'
    url = config.post_endpoint_url+str(post_id)
    print(url)
    response = requests.delete(url, headers=header)
    if response.status_code == 200:
        print(" deleted")
    else:
        print("error deleteing post:", response.status_code)
#------------------------------------------------------------------------------------------------
appserver_token=''
login_timestamp = datetime.datetime.now() # used to login once every hour
# seconds_between_logins = 60 # moving it to config.py
#------------------------------------------------------------------------------------------------
with open(config.blog_processor_log,'a') as f:

    while True:
        # Wait for a message in the queue
        key, message = redis_client.blpop(stream_name)

        print('\n','incoming message:\n',message)
        
        # load the message into a dictionary
        action_dict = json.loads(message)
        
        # check number of messages left in the queue and displya
        queue_length = redis_client.llen(stream_name)

        #--------------------------------------------------------------------
        if action_dict['action'] == 'seasonal_report':
        
            financial_group_id = int(action_dict['id'])
            date1              = action_dict['date']
            days_hold          = int(action_dict['days_hold'])
            symbol             = action_dict['symbol']
            years              = action_dict['years']
            zero_last_year     = eval(action_dict['zero_last_year'])
            base_year          = action_dict['base_year']
            category           = int(action_dict['category'])
            userid             = action_dict['userid']
            request_datetime   = action_dict['request_datetime']
            user_level         = action_dict['user_level']
            title              = action_dict['title']
            slug               = action_dict['slug']

            print('title and slug',title,slug)

            ########## get the reports_list saved in redis for reference ########### 
            redis_key_date_range_reports_list = 'date_range_reports_list' # all created reports listed in this key
            reports_list = redis_client.get(redis_key_date_range_reports_list)

            if reports_list is not None: 
                reports_list = json.loads(reports_list)
            else:
                reports_list = []

            # search the reports_list for a record with slug and userid - if its the same slug and userid, its because the user clicked the 
            # report link.  ignore it because its already on the list.  it will be useful if the report is deleted and removed from 
            # reports list then it will recreate it.
            current_rec_already_in_list = False
            lst = [item for item in reports_list if item['slug'] == slug] # check records with this slug - if multiple userid has to be differnt
            if len(lst) > 0 : 
                for rec in lst:
                    if rec['userid'] == userid:
                        current_rec_already_in_list = True
                        break
            


            
            # login to appserver here and get token
            if appserver_token == '':
                keyprovider_token = get_keyprovider_token()
                appserver_token   = login_appserver(keyprovider_token)
                login_timestamp = datetime.datetime.now() # used to login once every hour
            else: # there is a token - let's check if an hour has passed to login again
                test_timestamp = datetime.datetime.now()
                seconds_since_login = (test_timestamp - login_timestamp).total_seconds()
                if seconds_since_login > config.seconds_between_logins: # login again - 
                    keyprovider_token = get_keyprovider_token()
                    appserver_token   = login_appserver(keyprovider_token)
                    login_timestamp = datetime.datetime.now() # used to login once every hour
                    print('########################################')
                    print('############## login again #############')
                    print('########################################')

            post_id,msg,slug=generate_report(appserver_token,financial_group_id,date1,days_hold,symbol,years,zero_last_year,base_year,category)
            # return is post_id,message,slug

            action_dict['slug'] = slug
            action_dict['post_id'] = post_id

            # this one if for a list used by date range opportunities delete function
            log_dict = {
                'userid'        : userid,
                'user_level'    : user_level,
                'slug'          : slug,
                'post_id'       : post_id,
                'req_datetime'  : request_datetime
            }

            # now log this:
            if   msg == 'skip'                 : action_dict['result'] = 'skipped '
            elif msg == '201'                  : action_dict['result'] = 'created '
            else                               : action_dict['result'] = 'failed:'+msg+' '+slug
            #-------------------------------------------------------------------------------------------------------
            # if the category is a date range opportunity, add it to redis_key_date_range_reports_list
            # this reports_list is used when deleting reports - if more than 1 user point to a report don't delete
            #-------------------------------------------------------------------------------------------------------
            if category == config.category_date_range_report:
                
                redis_key_date_range_reports_list = 'date_range_reports_list' # all created reports listed in this key
                reports_list = redis_client.get(redis_key_date_range_reports_list)

                if reports_list is not None: 
                    reports_list = json.loads(reports_list)
                else:
                    reports_list = []

                # should not have duplications - 1 slug can be in 2 different records as long as userid of the two recs are different
                if not current_rec_already_in_list: reports_list.append(log_dict) # only append if a new report is created
                # load updated reports_list back into redis 

                # print('....................reports_list=',len(reports_list) )

                redis_client.set(redis_key_date_range_reports_list,json.dumps(reports_list))

            #-------------------------------------------------------------------------------------------------------
            # also append it to a log file - both categories of reports - seasonal reports and date range reports
            json_str = json.dumps(action_dict, indent=4)
            f.write(json_str+',\n')
            f.flush()

            print('post_id,message,slug=',post_id,msg,slug)
        
        #--------------------------------------------------------------------
        if action_dict['action'] == 'opp_list_blog': # new one 5/20/2023
            opp_date = action_dict['date']
            id       = int(action_dict['id'])
            run_opp_blog_generation(id,opp_date)
        #--------------------------------------------------------------------
        if action_dict['action'] == 'opp_list_blog_w_thumbnails': # new one 6/15/2023
            opp_date = action_dict['date']
            id       = int(action_dict['id'])
            run_opp_blog_generation_thumbnails(id,opp_date)
        #--------------------------------------------------------------------
        if action_dict['action'] == 'top10_page_by_date':# new one 5/20/2023
            opp_date             = action_dict['date']
            run_top10_page(opp_date)
        #--------------------------------------------------------------------
        if action_dict['action'] == 'top10_page_based_on_sr':# new one 5/20/2023
            opp_date             = action_dict['date']

            # check if this exists, otherwise skip it
            title_top10_by_sr,slug_top10_by_sr,post_id=top10_by_sr_title(opp_date)
    
            if post_id == -1: # -1 is not found
                df = load_top10_based_on_sr(opp_date)
                dfx=run_opp_blog_generation_thumbnails_sr(df,opp_date,title_top10_by_sr,slug_top10_by_sr)
                print('created top10_page_based_on_sr for:',opp_date)
            else:
                print('skipped top10_page_based_on_sr for:',opp_date)

        #--------------------------------------------------------------------
        if action_dict['action'] == 'archive_list':# new one 5/20/2023
            run_creating_archive ()
        #--------------------------------------------------------------------

        #--------------------------------------------------------------------
        if action_dict['action'] == 'delreport':
            userid = action_dict['userid']
            slug   = action_dict['slug']


            redis_key_date_range_reports_list = 'date_range_reports_list' # all created reports listed in this key
            reports_list = redis_client.get(redis_key_date_range_reports_list)

            if reports_list is not None: 
                reports_list = json.loads(reports_list)
                # search for the report in this list
                lst = [item for item in reports_list if item['slug'] == slug]

                # print('\nlst=',lst,'\n')
                # print('\nslug=',slug,'\n')

                if len(lst) == 1: # only 1 item, remove it and delete the report from wordpress
                    reports_list = [item for item in reports_list if item['slug'] != slug]
                    
                    # remove the blog from wordpress
                    del_report(slug,lst[0]['post_id'])
                    # print('removing the blog and deleteing the images')
                    
                elif len(lst) > 1 : 
                    reports_list = [item for item in reports_list if item['slug'] != slug or item['userid'] != userid]
                    # print('keeping the blog - deleting reference for the user')

                else:
                    print("error didn't find this slug to delete")
                    print('slug=',slug)
                    print('Full List=',reports_list,'\n')
                    

                # set the new list in redis
                redis_client.set(redis_key_date_range_reports_list,json.dumps(reports_list))  
        #--------------------------------------------------------------------
        # afshin date range reports to social media
        #--------------------------------------------------------------------
        if action_dict['action'] == 'am_dr_sm':

            # post on facebook

            ret=post_one_of_am_dr_reports(action_dict)
            print('action:am_dr_sm returned ',ret)


        #--------------------------------------------------------------------
          # Del afshin date range reports to social media
        #--------------------------------------------------------------------
        if action_dict['action'] == 'am_dr_sm_del':

            # post on facebook


            ret=del_one_of_am_dr_reports(action_dict)
            print('action:am_dr_sm returned ',ret)


        #--------------------------------------------------------------------
        if action_dict['action'] == 'cleanup':

            num_days_old = action_dict['num_days_old'] # if == 0 ignore - >0 : delete the posts that are at least num_days_old
            category     = action_dict['category']     # if == 0 ignore - otherwise delete with the category
            slug         = action_dict['slug']         # delete this individual slug
            post_id      = action_dict['post_id']      # delete this post_id
            max_total    = action_dict['max_total']    # if == 0 ignore - otherwise if # posts > max_total - delete oldest
            userid       = action_dict['user_id']

            print('num_days_old,category,userid,slug,post_id,max_total=',num_days_old,category,userid,slug,post_id,max_total)

        # check number of messages left in the queue and displya
        queue_length = redis_client.llen(stream_name)
        print('\n queue_length=',queue_length,)


    #--------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host='0.0.0.0',debug=True)
