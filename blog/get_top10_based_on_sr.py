
# this script gets today's top10 lists for all 12 markets and finds the top10 opportunities based on sharpe ratio




import pandas as pd
import datetime
from datetime import timedelta
import mailerlite as MailerLite
import sys
import requests
import json
import pprint
import time
from get_top10_data import load_top10,get_chart_data
from blog_tools import json_log, convert_param_base64,top10_link,get_keys
sys.path.insert(0, '/home/flask')
import config
import redis


#-----------------------------------------------------------------------------------------------------


# this function loads all the data for sending emails to users
# it selects the top opportunity only unless its a duplicate.  then
# it selects the next opportunity until it finds a unique one
# row_pos contains the number of opportunity selected, typically 0
def load_top10_based_on_sr():

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


    # concatenate all the dataframes and return the top10 based on Sharpe Ratio
    for key, df in dfd.items():
        df['resource_id'] = key
        df['market_name'] = config.available_resources[str(key)]

    # Concatenate all dataframes into one large dataframe
    dfc = pd.concat(dfd.values(), ignore_index=True)
    # sort by sharpe ratio
    dfc = dfc.sort_values(by='Sharpe Ratio', ascending=False)
    dfc = dfc[:10].reset_index(drop=True) # keep the top10

    return dfc
#-----------------------------------------------------------------------------------------------------



    
#-----------------------------------------------------------------------------------------------------
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    dfc  = load_top10_based_on_sr()
 
    # print(dfc)

    print(dfc.columns)

  
  


    
   
      


    






