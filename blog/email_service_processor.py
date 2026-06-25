# email service processor service is called by the local wordpress for communication with
# 1) email service provider - currently mailerlite
# 2) redis database on the application server

# the service is invoked when a user is registered or a user has upgraded or downgraded subscription level
# it updates both the mailerlite and tradewave redis database on appserver with any updates to user profile

# this service is envoked synchronously

from flask import Flask,jsonify,request
import os
import time
import redis
import json
import datetime
import sys
import requests

import pprint

from match_records import get_user_name_and_email_ump,get_keys,add_to_mailerlite
from email_tools import get_subscriber_by_email,get_email_groups,assign_subscriber_to_a_group,unassign_subscriber_from_a_group

sys.path.insert(0, '/home/flask')
import config

# redis = redis.Redis(host='localhost', port=6379, db=1)
# redis_client = redis.Redis(host='localhost', port=6379, db=0)
redis_client  = redis.Redis(host='localhost', port=6379, db=0)
redis_client2 = redis.Redis(host=config.appserver_ip, port=6379, db=2)  # used as a db

valid_ump_subscriptions =[ 'ripple' , 'tidal_yearly', 'tidal_monthly', 'surf_yearly', 'surf_monthly', 'splash_yearly', 'splash_monthly']

app = Flask (__name__)

#--------------------------------------------------------------------------------------------------------------------------------------------------------------
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
#--------------------------------------------------------------------------------------------------------------------------------------------------------------

#--------------------------------------------------------------------------------------------------------------------------------------------------------------

#--------------------------------------------------------------------------------------------------------------------------------------------------------------
@app.route('/')
def home():
    ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
    return jsonify({'message' : 'email interface processor 1.0 - ip:'+ip ,'length':len(ip) })

#--------------------------------------------------------------------------------------------------------------------------------------------------------------
# this thing processes new user being added or modified by adding or updating records in redis db on appserver and emailservie which is mailerlite on 9/6/2023
@app.route('/user_process/<int:userid>/<string:triggered_by>', methods=['GET'])
def user_process(userid,triggered_by):
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

    # print(first_name,last_name,email)

    user_level_name=get_user_level(userid)
    if user_level_name == 'None': # this user doesn't exist
        return jsonify({'message': 'userid does not exist'})

    #--------------------------------------------------------------------------------------
    # 1 - check if user is in redis db
    # I don't really need redis anymore - if settings is unused on wave viewer
    #--------------------------------------------------------------------------------------
    # redis_record = redis_client2.get(f'user_email_settings_{userid}')
    # if redis_record == None:
    #     user_redis = {}
    # else:
    #     user_redis= json.loads(redis_record)
    #--------------------------------------------------------------------------------------
    # 2 - check if user is in mailerlite
    #--------------------------------------------------------------------------------------
    mailerlite_dict = get_subscriber_by_email(email)

    if 'message' in mailerlite_dict: # not found in mailerlite
        user_mailerlite = {} # user email didn't exist in mailerlite
    else:
        user_mailerlite = mailerlite_dict['data']
    #--------------------------------------------------------------------------------------
    # 3 - add new user to mailerlite or modify existing user in mailerlite
    #--------------------------------------------------------------------------------------
    email_subscriber_id=add_to_mailerlite(first_name,last_name,email)
    #--------------------------------------------------------------------------------------
    # 4 - add / modify groups for the user in mailerlite 
    #--------------------------------------------------------------------------------------
    dict_groups=get_email_groups() # these are all the group names and groupid in mailerlite
    # now check what groups if any the user already has in mailerlite
    # first update the subscription group
    # print('mailerlite_dict=',mailerlite_dict)
    mailerlite_subscription_group = ''
    if 'message' in mailerlite_dict:
        print('returned message:',mailerlite_dict['message'])
    elif 'groups' in mailerlite_dict['data']:
        groups = mailerlite_dict['data']['groups']
        user_groups={}
        for g in groups:
            user_groups[g['name']]=g['id']
            if g['name'] in valid_ump_subscriptions:
                mailerlite_subscription_group = g['name']
    # user_groups is a dictionary of the current user's groups and the group id
    # update the user's subscription group
    ump_subscription = user_level_name # this was queried from ump earlier - this lineis for ease of reading the code
    # if ump subscription level is different than mailerlite, update mailerlite subscription group to the ump one
    if ump_subscription != mailerlite_subscription_group:
        # remove mailerlite_subscription_group
        if mailerlite_subscription_group != '': # mailerlite_subscription_group is only blank when user is new
            unassign_subscriber_from_a_group(email_subscriber_id,dict_groups[mailerlite_subscription_group])
        else: # there is no record for this user in mailerlite - new user
            # assign daily-top10-email-opt-in for this user
            assign_subscriber_to_a_group(email_subscriber_id,dict_groups['daily-top10-email-opt-in'])
        # add the new subscription group ump_subscription
        assign_subscriber_to_a_group(email_subscriber_id,dict_groups[ump_subscription])
    # check user on redis - 

    #--------------------------------------------------------------------------------------
    # 5 - create new record or update existing record in redis - include mailelite userid
    #--------------------------------------------------------------------------------------
    # if user_redis == {}: # new user to be added to redis

    # I don't think I need redis anymore

    # user_redis['first_name']            = first_name
    # user_redis['last_name']             = last_name
    # user_redis['email']                 = email
    # user_redis['email_subscriber_id']   = email_subscriber_id 
    # user_redis['wp_userid']             = str(userid)
    # user_redis['level_id_name']         = ump_subscription
    # user_redis['top10_email_state']     = 1 # initially opt-in the user for top10 emails
    # redis_client2.set(redis_key_email_settings,json.dumps(user_redis))
          

    return jsonify({'message': 'success'})

###########################################################################################################
if __name__ == "__main__":
    app.run(host='0.0.0.0',debug=True)
