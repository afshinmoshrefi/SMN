# apigate setting in ump need to turn on api : Activate/Hold "Get all User Data" API Call
import redis
import json
import pandas as pd
import sys
import requests
import pprint
import mailerlite as MailerLite
import pprint
import datetime
from datetime import timedelta
from get_top10_data import load_top10,get_chart_data



sys.path.insert(0, '/home/flask')
import config

mailerlite_token = config.mailerlite_token
#-----------------------------------------------------------------------------------------------------
def create_subscriber(email,first_name,last_name,ip,optin_ip):
    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.create(email, fields={'name': first_name, 'last_name': last_name}, ip_address=ip, optin_ip=optin_ip)
    return response
#-----------------------------------------------------------------------------------------------------
def get_list_subscribers():
    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.list(limit=10, page=1, filter={'status': 'active'})
    return response
#-----------------------------------------------------------------------------------------------------
def create_campaign(campaign_name,subject,from_name,from_email,content):
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
        }]
    }

    response = client.campaigns.create(params)
    pprint.pprint(response['data'])
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
    print('scheduling')
    response = client.campaigns.schedule(campaign_id, params)
    print(response)
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
def assign_subscriber_to_a_group(subscriber_id,group_id):

    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.assign_subscriber_to_group(subscriber_id, group_id)
    print(response)
#-----------------------------------------------------------------------------------------------------
# finds subscriber by email, and updates first and lastname
def update_subscriber(email,first_name,last_name):

    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.update(email, fields={'name': first_name, 'last_name': last_name}, ip_address='', optin_ip='')
    
    return response
#-----------------------------------------------------------------------------------------------------
def delete_subscriber(subscriber_id):
    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.delete(subscriber_id)
    return response
#-----------------------------------------------------------------------------------------------------
def get_subscriber(email):
    client = MailerLite.Client({'api_key': mailerlite_token })
    response = client.subscribers.get(email)

    print(response['data']['groups'])

    first_name = response['data']['fields']['name'] 
    last_name = response['data']['fields']['last_name']
    groups_response = response['data']['groups']
    
    groups = []
    for g in groups_response:
        groups.append({'name':g['name'],'id':g['id']})

    return first_name,last_name,groups    
#-----------------------------------------------------------------------------------------------------
def get_email_groups():
    client = MailerLite.Client({'api_key': config.mailerlite_token })
    response = client.groups.list(limit=100, page=1, sort='name')

    dict_tt = {} # all the tt_ groups
    dict_ot = {} # all the other groups

    for g in response['data']:
        if g['name'][:3] == 'tt_':  # we only want tt_ top10 groups here
            name = g['name'][3:] # strip tt_ - the name has to be tt_ in mailerlite to seperate from other groups
            g_id = g['id']
            dict_tt[name]=g_id
        else:
            name = g['name'] # strip tt_ - the name has to be tt_ in mailerlite to seperate from other groups
            g_id = g['id']
            dict_ot[name]=g_id

        # print('dict_tt=',dict_tt)

    return dict_tt,dict_ot
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':
#-----------------------------------------------------------------------------------------------------
    

    dict_tt,dict_ot = get_email_groups()
    print(dict_ot)

    exit()
    # first_name,last_name,groups=get_subscriber('afshinmoshrefi@hotmail.com')
    # print(first_name,last_name,groups)

    # print(groups[0])



    # response=update_subscriber('afshinmoshrefi@hotmail.com','Afshin','Moshrefi')
    # print(response)

    # response=delete_subscriber(92595253540816273)
    # print(response)


    exit()
    filename = config.today_top10_data
    print(filename)
    # dfd is a dictionary of dataframes with the keys for each of the 12 financial groups
    action,dfd=load_top10 (filename)

    print(dfd[0].columns)

    

    fg_lst = []
    i_lst  = []
    sym_lst= []
    ap_lst = []
    sr_lst = []

    for fg in list(config.available_resources.keys()):
        i = 0 # only the first 1 is used
        fgi = int(fg)   

        symbol = dfd[fgi].iloc[i]['Symbol']
        avg_profit=dfd[fgi].iloc[i]['Avg Profit']
        sr=dfd[fgi].iloc[i]['Sharpe Ratio']
        
        fg_lst.append(fgi)
        i_lst.append(i)
        sym_lst.append(symbol)
        ap_lst.append(avg_profit)
        sr_lst.append(sr)
    df = pd.DataFrame()
    df['fg'] = fg_lst
    df['i']  = i_lst
    df['symbol'] = sym_lst
    df['avg_profit'] = ap_lst
    df['sharpe ratio']=sr_lst
    # df['avg_profit2'] = df['avg_profit'].str.rstrip('%').astype(float)

    print(df)

    exit()

    campaign_id,campaign_date_time = create_campaign('my test campaign','test subject','afshin desk','afshin@tradeseasonals.com','test content')
    print(campaign_id,campaign_date_time)

    d,h,m=future_date_hour_min(2) # get date hour and min of # minutes from now, whatever minutes number is passed.

    schedule_campaign (campaign_id,d,h,m) # must schedule to send