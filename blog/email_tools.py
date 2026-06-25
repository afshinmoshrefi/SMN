# this file contains all the tools to connect to the email service - currently mailerlite

# from email_tools import create_subscriber,get_all_subscribers
# from email_tools import get_users_from_redis,create_mailerlite_group,update_mailerlite,get_email_groups
# from email_tools import assign_subscriber_to_a_group,unassign_subscriber_from_a_group,get_num_subscribers
# from email_tools import create_campaign,schedule_campaign,today_date_hour_min,future_date_hour_min

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

# this list is used to recognize the groups that are based on user level name
valid_ump_subscriptions =[ 'ripple' , 'tidal_yearly', 'tidal_monthly', 'surf_yearly', 'surf_monthly', 'splash_yearly', 'splash_monthly']


#-----------------------------------------------------------------------------------------------------
def create_subscriber(email,first_name,last_name,ip,optin_ip):
    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.create(email, fields={'name': first_name, 'last_name': last_name}, ip_address=ip, optin_ip=optin_ip)
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
    fdate = future_datetime.strftime("%Y-%m-%d")
    
    # Format the time as '%H:%M:%S'
    ftime = future_datetime.strftime("%H:%M:%S")

    d = fdate[:10]
    h = ftime[:2]
    m = ftime[3:5]

    return d,h,m

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
def get_num_subscribers(group_id):

    client = MailerLite.Client({'api_key': config.mailerlite_token })
    response = client.groups.get_group_subscribers(group_id, page=1, limit=10, filter={'status': 'active'})

    subscribers = response['data']
    return len(subscribers)
#-----------------------------------------------------------------------------------------------------
def get_all_subscribers():
    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.list(limit=10, page=1)
    return response
#-----------------------------------------------------------------------------------------------------
def get_subscriber_by_email(email):
    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.get(email)
    return response
#-----------------------------------------------------------------------------------------------------
def get_user_level(userid):
    _,key2 = get_keys()
    url = f'{config.wordpress_url}?ihc_action=api-gate&ihch={key2}&action=get_user_levels&uid={userid}'
    response = requests.get(url)
    d = response.json()
    # first check to make sure this user only has 1 level
    # if more than 1 level which is wrong, take the largest one
    if len(d['response']) == 0:
        return 'None' # something went wrong - this user doesn't exist in ump / wp
    levels = list(d['response'].keys())
    print('levels=',levels,len(levels))
    level = '0'
    for l in levels:
        if l > level:
            level = l

    # now level is the user's highest level is case user has more than 1 level which shouldn't happen but its supported in case of a mistake adding multiple levels

    level_info = d['response'][level]
    level_name = level_info['level_slug']
    
    return level_name
#----------------------------------------------------------------------------------------------------------

def get_user_name_and_email_ump(userid,key2):
    _,key2 = get_keys()
    url = f'{config.wordpress_url}?ihc_action=api-gate&ihch={key2}&action=user_get_details&uid={userid}'

    
    response = requests.get(url)
    d = response.json()
    

    if len(d['response']) == 0:
        return '','',''


    first_name = d['response']['first_name']
    last_name  = d['response']['last_name']
    email      = d['response']['user_email']

    
    return first_name,last_name,email 
#----------------------------------------------------------------------------------------------------------
def add_to_mailerlite(first_name,last_name,email):
    email_subscriber_id=0

    client = MailerLite.Client({'api_key': config.mailerlite_token })
    response = client.subscribers.create(email, fields={'name': first_name, 'last_name': last_name}, ip_address='', optin_ip='')
    email_subscriber_id = response['data']['id']

    return email_subscriber_id
#----------------------------------------------------------------------------------------------------------
def delete_from_mailerlite(email):

    # get subscriber_id for this user from email
    mailerlite_dict = get_subscriber_by_email(email)

    response = ''

    if 'message' in mailerlite_dict: # not found in mailerlite
        return f'{email} address not found'
    else:
        user_mailerlite = mailerlite_dict['data']

    subscriber_id = int(user_mailerlite['id'])

    #----------------------------------------------------------------------------------
    # delete the client from mailerlite - this does not forget the client
    # client = MailerLite.Client({'api_key': config.mailerlite_token })
    # response = client.subscribers.delete(subscriber_id)
    #----------------------------------------------------------------------------------

    #----------------------------------------------------------------------------------
    # instead of deleting the user in mailerlite, add them to the group opt-out-all
    dict_groups = get_email_groups()
    group_id = dict_groups['opt-out-all']
    assign_subscriber_to_a_group(subscriber_id,group_id)
    #----------------------------------------------------------------------------------

    return response
#----------------------------------------------------------------------------------------------------------
# this function reads ump and mailerlite and updates mailerlite accordingly with new user or modified user
# basically reconsiles user info between ump and mailerlite 9/7/2023
#----------------------------------------------------------------------------------------------------------
def process_user_email_service(userid):
    #-------------------------------------------------------------------------------------
    # 0 - get user information from UMP
    # 1 - check if user is in redis db
    # 2 - check if user is in mailerlite
    # 3 - add new user to mailerlite or modify existing user in mailerlite
    # 4 - add / modify groups for the user in mailerlite 
    # 5 - create new record or update existing record in redis - include mailelite userid
    #-------------------------------------------------------------------------------------
    redis_key_email_settings = f'user_email_settings_{userid}'
    #--------------------------------------------------------------------------------------
    # 0 - get user information from UMP
    #--------------------------------------------------------------------------------------
    _,key2 = get_keys()
    first_name,last_name,email=get_user_name_and_email_ump(userid,key2)

    if email == '':
        return 'userid not found in ump'

    # print(first_name,last_name,email)

    user_level_name=get_user_level(userid)
    if user_level_name == 'None': # this user doesn't exist
        print(f'user id {userid} not found in ump')
        return  'userid does not exist'

    #--------------------------------------------------------------------------------------
    # 3 - add new user to mailerlite or modify existing user in mailerlite
    #--------------------------------------------------------------------------------------
    email_subscriber_id=add_to_mailerlite(first_name,last_name,email)
    #--------------------------------------------------------------------------------------
    # 2 - get info about the user again.  sometimes a deleted user coming back has some groups
    #--------------------------------------------------------------------------------------
    mailerlite_dict = get_subscriber_by_email(email)

    if 'message' in mailerlite_dict: # not found in mailerlite
        user_mailerlite = {} # user email didn't exist in mailerlite
    else:
        user_mailerlite = mailerlite_dict['data']

    #--------------------------------------------------------------------------------------
    # 4 - add / modify groups for the user in mailerlite 
    #--------------------------------------------------------------------------------------
    dict_groups=get_email_groups() # these are all the group names and groupid in mailerlite
    # now check what groups if any the user already has in mailerlite
    # first update the subscription group
    # print('mailerlite_dict=',mailerlite_dict)
    mailerlite_subscription_group = ''
    user_groups={} # put this user's groups in this dict if user has any groups
    if 'message' in mailerlite_dict:
        print('mailerlite returned message:',mailerlite_dict['message'])
    elif 'groups' in mailerlite_dict['data']: # this user has group assignments
        groups = mailerlite_dict['data']['groups']
        
        for g in groups:
            user_groups[g['name']]=g['id']
            if g['name'] in valid_ump_subscriptions:
                # check if this is the same as ump subscription level name - if not remove it
                if g['name'] != user_level_name:
                    unassign_subscriber_from_a_group(email_subscriber_id,dict_groups[g['name']])
                else:
                    mailerlite_subscription_group = g['name']

    assign_subscriber_to_a_group(email_subscriber_id,dict_groups[user_level_name]) # assign the ump user level name to mailerlite
    # check if 'daily-top10-email-opt-in' or 'daily-top10-email-opt-out' is assigned to the user
    # if not give then opt-in as the default.  if opt-out is there leave it as is
    # print ('user_groups=-',user_groups)
    opt_in  = 'daily-top10-email-opt-in'  in user_groups.keys()
    opt_out = 'daily-top10-email-opt-out' in user_groups.keys()

    if opt_in and opt_out: # both daily-top10-email-opt-in and daily-top10-email-opt-out are assigned to user
        unassign_subscriber_from_a_group(email_subscriber_id,dict_groups['daily-top10-email-opt-out'])
    elif not opt_in and not opt_out: # neither daily-top10-email-opt-in and daily-top10-email-opt-out are assigned to user
        assign_subscriber_to_a_group(email_subscriber_id,dict_groups['daily-top10-email-opt-in'])

    # opt-out-all is assigned to a user that is deleted from UMP - the record is kept but opt-out-all is addedif they are added again, 
    # if user is added again make sure to remove this group 
    if 'opt-out-all' in user_groups.keys():
          unassign_subscriber_from_a_group(email_subscriber_id,dict_groups['opt-out-all'])

    return 'success'
#-----------------------------------------------------------------------------------------------------
def del_user_from_email_service(email):
    pass
#-----------------------------------------------------------------------------------------------------
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    # dict_groups=get_email_groups()
    # print(dict_groups)

    # client = MailerLite.Client({'api_key': config.mailerlite_token })
    # response = client.groups.get_group_subscribers(group_id, page=1, limit=10, filter={'status': 'active'})

    # subscribers = response['data']
    process_user_email_service(110)
   
   
      


    






