import pandas as pd
import mailerlite as MailerLite
import sys
import requests
import json
import redis
sys.path.insert(0, '/home/flask')
import config
from generate_emails import create_subscriber,get_users_from_redis,assign_subscriber_to_a_group,create_mailerlite_group,get_email_groups



mailerlite_token = config.mailerlite_token



#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':


    create_mailerlite_group('tt_000000000000') # no emails
    create_mailerlite_group('tt_111110000001') # stocks & ETFs
    create_mailerlite_group('tt_000001111110') # F&C and others
    create_mailerlite_group('tt_111111111111') # everything
    create_mailerlite_group('subscriber')

    df = pd.read_csv('TS-STAGE-USERS-2023-07-01.csv')

    # print(df.columns)
    df=df[['User ID', 'Username', 'Email', 'First Name', 'Last Name','Membership ID']]


    # connect to the remote redis on the appserver - db=2 - add blank email preferences for all the users
    host_ip = config.appserver_ip # if running on local - ip for the appserver is in config
    redis_client2 = redis.Redis(host=host_ip, port=6379, db=2)  # used as a db

    df_redis_users = get_users_from_redis()
    # this should really run on the production 

    print(df_redis_users)

    # get a dictionary of groups and their group_id - we need group_id to assign and unassign to from groups
    dict_email_groups_tt,dict_email_groups_ot=get_email_groups() 

    print(df)


    # use the dataframe to load all to mailerlite and then redis db=2 - email_settings
    for i,r in df.iterrows():

        first_name = r['First Name']
        last_name = r['Last Name']
        email = r['Email']
        userid = r['Membership ID']

        


        if 'admin' in email:
            continue

        print(first_name,last_name,email,userid)
        print('')


        ip = ''
        optin_ip = ''
        response=create_subscriber(email,first_name,last_name,ip,optin_ip)
        email_subscriber_id = response['data']['id']

        # assign this user to group 000000000000 for new users when added to mailerlite
        group_id = dict_email_groups_tt['000000000000']

        # check if this user is already in redis:
        redis_key_email_settings = f'user_email_settings_{userid}' 
        redis_user_email_settings = redis_client2.get(redis_key_email_settings)

        if redis_user_email_settings is None:
            redis_client2.set(redis_key_email_settings,json.dumps({'top_num':1,'flags':'000000000000','first_name':first_name,'last_name':last_name,'email':email,'email_subscriber_id':email_subscriber_id,'wp_userid':userid}))
            assign_subscriber_to_a_group(email_subscriber_id,group_id)
        else: # this user is already in redis - make sure userid and subscriber_id are correctly set
            email_settings_dict = json.loads(redis_user_email_settings)
            email_settings_dict['email_subscriber_id']=email_subscriber_id
            email_settings_dict['wp_userid']=userid
            redis_client2.set(redis_key_email_settings,json.dumps(email_settings_dict))

