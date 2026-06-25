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



#---------------------------------------------------------------------------------------

def get_keyprovider_token():
    url = config.appserver_url+'/login/2/3/4/5/6'
    api_result = requests.get(url)
    result = api_result.json()
    t = result['message'].split(' ')
    return t[4]
#---------------------------------------------------------------------------------------
# after logging in, the returned token is used to make other calls to the appserver
def login_appserver(keyprovider_token):
    url = config.appserver_url+'/login/28/3/4/5/'+keyprovider_token
    api_result = requests.get(url)
    result = api_result.json()
    return result['token']
#---------------------------------------------------------------------------------------
def get_opp_list(group_id, month,day,years,pyears,appserver_token):
    
    # urlX = config.appserver_url+'/OppList4/'+str(group_id)+'/'+month+'/'+day+'/'+str(years)+'/'+str(pyears)+'/0/0?token='+appserver_token
    urlX = f'{config.appserver_url}/OppList4/{group_id}/{month}/{day}/{years}/{pyears}/-/0/0?token={appserver_token}'
    api_result = requests.get(urlX)
    if api_result.status_code != 200: 
        print('Error occured ',api_result.status_code)
        exit()
    result = api_result.json()
    return result

#---------------------------------------------------------------------------------------
def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')
#----------------------------------------------------------------------------------------------------
#  return years and pyears based on which resource group from 0 or 11 is selected
#------------------------------------------------------------------------------------------------
def get_years_pyears_from_resource_id(id):
    years    = 10  
    pyears   = 10
    if id == 0 or id == 5 or id == 11  or id==7 : 
        pyears = 9 # not enough results when returning 
    if id == 9: 
        pyears = 8 # forex liquid
    if id == 10  :  # this is bonds 
        years = 8
        pyears = 8
    return years,pyears
#------------------------------------------------------------------------------------------------

#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    # today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)


    # this creates a sequence
    date1 = '2023-01-02'
    date2 = '2023-12-31'
    diff = datetime.datetime.strptime(date2, '%Y-%m-%d') - datetime.datetime.strptime(date1, '%Y-%m-%d')
    num_days = diff.days

    id = 0

    big_list = []




    t1 = time.time()

    for i in range(num_days+1):
        d = inc_date_day(date1,i)
        
        month     = calendar.month_name[int(d[5:7])]
        day       = str(int(d[8:]))


        # print(d,month,day)
        years,pyears = get_years_pyears_from_resource_id(id)
        result = get_opp_list(0, month,day,years,pyears,appserver_token)
        lst = result['OppList']

        if len(lst)>10:lst=lst[:10]

        big_list += lst
        print(i,end='\r')
    t2 = time.time()

    print("Execution time:", t2-t1, "seconds")    
    print('length of list of lists=',len(big_list))
    
    # opp_logs is the location to save these files

    # pprint.pprint(big_list)

    df = pd.DataFrame(big_list,columns=['date','symbol','days','direction','sharpe-ratio'])

    print(df)
    df.to_csv(config.opp_logs+'opp_0.csv')
