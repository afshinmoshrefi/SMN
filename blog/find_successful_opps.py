
# this script scans throught the opportunity status files in 
# /home/flask/blog/logs/opp


import os
import pandas as pd
import sys
import pprint
import time
import requests
import calendar
import datetime
from datetime import timedelta
sys.path.insert(0, '/home/flask')
import config















#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    resource_id = 7

    opp_stat_file = f'{config.opp_logs}opp_{resource_id}.csv'

    # print(opp_stat_file)

    df = pd.read_csv(opp_stat_file)
    c  = df.columns
    ct = c[:8] 
    ct = ct.append(c[-2:])
    

    print(ct)

    df = df[ct] # creating a subset of the columns
    lcn= c[-1] # last colymn name
    for i,r in df.iterrows():
        if r[lcn]>0.2:
            # print(i,r['symbol'],r[lcn])
            print(r)
            print('')
        