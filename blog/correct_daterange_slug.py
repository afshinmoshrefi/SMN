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

# this script tries to correct the slug in the appserver redis db=2 
# this is used when I had to rebrand and URLs for the reports format changed.  this will correct the
# reports that already have been generated to the new slug format that has tradewave inthe slug
# all the reports with the old slug have to be deleted first for this to work

if __name__ == '__main__':


    # connect to the remote redis on the appserver - db=2 - add blank email preferences for all the users
    host_ip = config.appserver_ip # if running on local - ip for the appserver is in config
    redis_client2 = redis.Redis(host=host_ip, port=6379, db=2)  # used as a db

    df_redis_users = get_users_from_redis()
    # this should really run on the production 

    # print(df_redis_users)

    for i,r in df_redis_users.iterrows():
        # if r['first_name'] != 'afshin':continue

        wp_userid = r['wp_userid']
        print(r['first_name'],wp_userid)

        redis_key_user_reports = f'user_reports_{wp_userid}' 
        redis_user_reports_list = redis_client2.get(redis_key_user_reports)

        if redis_user_reports_list is not None: 
            redis_user_reports_list = json.loads(redis_user_reports_list)

            print('old list:','\n',redis_user_reports_list,'\n\n')

            new_redis_user_reports_list=[]

            for report_info in redis_user_reports_list:

                

                old_slug = report_info['slug']
                new_slug = old_slug.replace('date-range','tradewave')

                updated_dict = report_info.copy()

                updated_dict['slug']= new_slug

                new_redis_user_reports_list.append(updated_dict)

            print('new list:','\n',new_redis_user_reports_list,'\n\n')

            redis_client2.set(redis_key_user_reports,json.dumps(new_redis_user_reports_list))



    
    
