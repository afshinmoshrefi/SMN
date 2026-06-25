
# the user database in redis is on db=2 - on redis-cli have to type select 2  in order to switch to db=2
# user database is in json format user_email_settings_37  - started with email settings then I realized I should have
# called it a better name but its all the data about the user including email settings and preferences


# this python script reads redis, ump and mailerlite user dbs and compares them - ump is primary so if there is any 
# difference with mailerlite and redis records, mailerlite is updated

# I'm not sure if its necessary but it would correct any discrepencies if it occurs
# the master list should be wordpress user list

# I used this script to remove all the existing email preferences from redis and then 
# read all the UMP users and create new mailerlite subscribers and redis email preferences for all
# TradeWave prod users 7/14/2023


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
import base64


from email_tools import create_subscriber,get_all_subscribers
from email_tools import get_users_from_redis,create_mailerlite_group,update_mailerlite,get_email_groups
from email_tools import assign_subscriber_to_a_group,unassign_subscriber_from_a_group,get_num_subscribers
from email_tools import create_campaign,schedule_campaign,today_date_hour_min,future_date_hour_min



redis_client  = redis.Redis(host='localhost', port=6379, db=0)  # used as a cache
redis_client2 = redis.Redis(host=config.appserver_ip, port=6379, db=2)  # used as a db

mailerlite_token = config.mailerlite_token

#-----------------------------------------------------------------------------------------------------

#-----------------------------------------------------------------------------------------------------
def get_all_levels_from_ump(key2):

    url = f'{config.domain_root}?ihc_action=api-gate&ihch={key2}&action=list_levels'
    response = requests.get(url)
    r = response.json()

    levels_list = r['response']
    level_ids_list = [d['level_id'] for d in levels_list]

    # returning both just level numbers and level details list just in case detail is needed later
    return level_ids_list,levels_list


#-----------------------------------------------------------------------------------------------------

def get_user_name_and_email_ump(userid,key2):
    _,key2 = get_keys()
    url = f'{config.wordpress_url}?ihc_action=api-gate&ihch={key2}&action=user_get_details&uid={userid}'
    response = requests.get(url)
    d = response.json()


    first_name = d['response']['first_name']
    last_name  = d['response']['last_name']
    email      = d['response']['user_email']
    
    return first_name,last_name,email 
#-----------------------------------------------------------------------------------------------------
# this function will get all the users from ump - since ump apigate doesn't have get_all_users
# it first get a list of all memberships/levels - then makes a call to get all users for that level_id
# the it calls ump again for each user and gets first_name, last_name and email
# returns as a list of dictionaries - this will be show when number of users grow
#-----------------------------------------------------------------------------------------------------
def get_all_users_ump():

    _,key2 = get_keys()
    level_ids_list,memberships_detailed_list=get_all_levels_from_ump(key2)

    # print(memberships_detailed_list)
    # exit()
    
    # get users for each level_id
    users = []
    for mdl in memberships_detailed_list:
        lid = mdl['level_id']
        lid_name = mdl['slug']
        url = f'{config.domain_root}?ihc_action=api-gate&ihch={key2}&action=get_level_users&lid={lid}'
        print('processing level id:',lid)
        
        response = requests.get(url)
        r = response.json()
        users_with_this_level_id = r['response']

        for u in users_with_this_level_id:

            if 'username' in u and len(u['username'])>0:
                userid = u['user_id']
                first_name,last_name,email=get_user_name_and_email_ump(userid,key2)
                u['first_name'] = first_name
                u['last_name'] = last_name
                u['email'] = email
                u['level_id_name'] = lid_name
                u['level_id']= lid
                users.append(u)


    return users   
   
#-----------------------------------------------------------------------------------------------------
def get_users_from_redis():
    # query for all the keys that hold user_email_settings
   
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

    # df = pd.DataFrame(users_to_email)
    
    # make sure all the emails are in the mailerlite
    # also update flags in case user changed it 

    return users_to_email
#-----------------------------------------------------------------------------------------------------

#-----------------------------------------------------------------------------------------------------
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
# def get_email_groups():
#     client = MailerLite.Client({'api_key': config.mailerlite_token })
#     response = client.groups.list(limit=100, page=1, sort='name')

#     dict_tt = {} # all the tt_ groups
#     dict_ot = {} # all the other groups

#     for g in response['data']:
#         if g['name'][:3] == 'tt_':  # we only want tt_ top10 groups here
#             name = g['name'][3:] # strip tt_ - the name has to be tt_ in mailerlite to seperate from other groups
#             g_id = g['id']
#             dict_tt[name]=g_id
#         else:
#             name = g['name'] # strip tt_ - the name has to be tt_ in mailerlite to seperate from other groups
#             g_id = g['id']
#             dict_ot[name]=g_id

#         # print('dict_tt=',dict_tt)

#     return dict_tt,dict_ot
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
def add_to_mailerlite(first_name,last_name,email):
    email_subscriber_id=0

    client = MailerLite.Client({'api_key': config.mailerlite_token })
    response = client.subscribers.create(email, fields={'name': first_name, 'last_name': last_name}, ip_address='', optin_ip='')
    email_subscriber_id = response['data']['id']

    return email_subscriber_id
#-----------------------------------------------------------------------------------------------------


#-----------------------------------------------------------------------------------------------------    
def get_num_subscribers(group_id):

    client = MailerLite.Client({'api_key': config.mailerlite_token })
    response = client.groups.get_group_subscribers(group_id, page=1, limit=10, filter={'status': 'active'})

    subscribers = response['data']
    return len(subscribers)
#-----------------------------------------------------------------------------------------------------
def remove_all_mail_preferences_redis():
    
    keys = redis_client2.keys('user_email_settings_*')
    
    for k in keys:
        key = k.decode()
        redis_client2.delete(key)
    
#-----------------------------------------------------------------------------------------------------
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    initial_user_email_status = 1   # users that don't have a record in redis and mailerlite will get this status for top10 daily emails



    dict_email_groups= get_email_groups() # these are list of all groups in mailerlite

 

    users_ump        = get_all_users_ump()
    # users_mailerlite = get_all_subscribers()

  
    for user in users_ump:

        # get this record on redis
        redis_key_email_settings = f'user_email_settings_{user["user_id"]}' # this is a json version of a list of dictionaries
        redis_user_email_settings = redis_client2.get(redis_key_email_settings)

        # print(redis_user_email_settings)
        # print('')

        if redis_user_email_settings is None:
            # load info into mailerlite first so that we can get and store the email_subscriber_id in redis
            email_subscriber_id = add_to_mailerlite(user['first_name'],user['last_name'],user['email'])
            # load info into redis as a new record
            redis_client2.set(redis_key_email_settings,json.dumps({'first_name':user['first_name'],'last_name':user['last_name'],'email':user['email'],'email_subscriber_id':email_subscriber_id,'wp_userid':user['user_id'],'wp_user_level_name':user['level_id_name'],'wp_user_level':user['level_id'],'top10_email_state':initial_user_email_status}))
            # assign the user's subscription level group_id
            group_id = user['level_id_name'] # subscription level group
            assign_subscriber_to_a_group(email_subscriber_id,group_id)
            # assign the user's top10 email status group
            if initial_user_email_status == 1:
                group_id = dict_email_groups['daily-top10-email-opt-out'] # in case user already was in mailerlite 
                unassign_subscriber_from_a_group(email_subscriber_id,group_id)
                group_id = dict_email_groups['daily-top10-email-opt-in']
                assign_subscriber_to_a_group(email_subscriber_id,group_id)
            else:
                group_id = dict_email_groups['daily-top10-email-opt-in'] # in case user already was in mailerlite 
                unassign_subscriber_from_a_group(email_subscriber_id,group_id)
                group_id = dict_email_groups['daily-top10-email-opt-out']
                assign_subscriber_to_a_group(email_subscriber_id,group_id)

        else: # this user already has a record in redis - fix the record in case it was the old style and also if level changed
            
            d = json.loads(redis_user_email_settings)
            ##################################################################
            ##################################################################
            # afshin only right now while developing and testing
            if d['email'] != 'afshinmoshrefi@hotmail.com':
                continue
            ##################################################################
            ##################################################################
            if 'top_num' in d: del d['top_num'] # this is from the old days - key is deprecated
            if 'flags'   in d: del d['flags']   # this is from the old days - key is deprecated
            if 'top10_email_state' not in d: d['top10_email_state']=initial_user_email_status # incase key is missing
            # correct the records in case they have changed:
            d['first_name']=user['first_name']
            d['last_name']=user['last_name']
            d['email']=user['email']
            if d['wp_user_level'] != user['level_id']: # subscription level for the user has changed
                # change the subscription group tag for the user
                email_subscriber_id = d['email_subscriber_id']
                group_id = dict_email_groups[d['wp_user_level_name']] # unassign the old subscription level tag in mailerlite
                unassign_subscriber_from_a_group(email_subscriber_id,group_id)
                group_id = dict_email_groups[user['level_id_name']] # assign the new subscription level tag in mailerlite
                assign_subscriber_to_a_group(email_subscriber_id,group_id)
                d['wp_user_level']=user['level_id'] # update redis with the new level id and name
                d['wp_user_level_name']=user['level_id_name']
            # update redis with the modified record
            redis_client2.set(redis_key_email_settings,json.dumps(d))



             

            print('')
            print(d)
            exit()

            

  
    
      


    






