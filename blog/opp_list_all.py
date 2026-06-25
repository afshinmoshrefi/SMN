
# technically this program should run once a year to create the initial list of all the opportuntiies
# it will then be used daily to monitor the status of the past opportuniteis and check their gain and loss
# that will be used to get high gain opportuniteis that will be sent to social media for marketing
# this will be used for interim reports when one of the opps are making a lot, do an interim report

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
    # if id == 0 or id == 5 or id == 11  or id==7 : 
    #     pyears = 9 # not enough results when returning 
    # if id == 9: 
    #     pyears = 8 # forex liquid
    # if id == 10  :  # this is bonds 
    #     years = 8
    #     pyears = 8
    return years,pyears
#------------------------------------------------------------------------------------------------
# create_opp_lists
#------------------------------------------------------------------------------------------------
def create_opp_lists(id,date1,date2,appserver_token):

    opp_list_filename = f'{config.opp_logs}opp_{id}.csv'
    
    # if file exist skip it
    if os.path.exists(opp_list_filename):
        return


    big_list = []



    diff = datetime.datetime.strptime(date2, '%Y-%m-%d') - datetime.datetime.strptime(date1, '%Y-%m-%d')
    num_days = diff.days


    t1 = time.time()

    for i in range(num_days+1):
        d = inc_date_day(date1,i)
        
        month     = calendar.month_name[int(d[5:7])]
        day       = str(int(d[8:]))


        # print(d,month,day)
        years,pyears = get_years_pyears_from_resource_id(id)
        result = get_opp_list(id, month,day,years,pyears,appserver_token)
        lst = result['OppList']

        if len(lst)>10:lst=lst[:10]

        big_list += lst
        print(i,end='\r')
    t2 = time.time()

    print(f"Execution time for {id}:", t2-t1, "seconds")    
    print('length of list of lists=',len(big_list))
    
    # opp_logs is the location to save these files

    # pprint.pprint(big_list)

    df = pd.DataFrame(big_list,columns=['date','symbol','days','direction','sharpe-ratio'])

    # add date2
    # Calculate 'date2' column by adding 'days' to 'date1'

    df['date2'] = df.apply(lambda row: inc_date_day(row['date'], int(row['days']) ), axis=1)

    df = df[['date','date2','symbol','days','direction','sharpe-ratio']] # reorder and put date2 next to date

    # add a column for entry_price
    df['entry_price']=0
    


    df.to_csv(opp_list_filename)  # first tiem its saved won't use index=False


#------------------------------------------------------------------------------------------------
# stock price by date
#------------------------------------------------------------------------------------------------
def get_stock_price_by_date(id,symbol,date):
    urlX = f'{config.appserver_url}/getStockPriceByDate/{id}/{symbol}/{date}?token={appserver_token}'
    api_result = requests.get(urlX)
    result = api_result.json()
    return result
#------------------------------------------------------------------------------------------------
# get last stock price available
#------------------------------------------------------------------------------------------------
def get_last_price_date(id,symbol):
    urlX = f'{config.appserver_url}/StockLastPrice/{id}/{symbol}?token={appserver_token}'
    api_result = requests.get(urlX)
    r = api_result.json()

    last_price = r['StockLastPrice'][1]
    last_date  = r['StockLastPrice'][0]

    return last_date,last_price
#------------------------------------------------------------------------------------------------
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)

    resource_id = 1

    # last_date,last_price=get_last_price_date(0,'MSFT')
    # print(last_date,last_price)
    # exit()

    # this creates a sequence
    date1 = '2023-01-03'
    date2 = '2023-12-31'
    # this creates the initial files used to track daily progress of each opportunity - needs to run once a year
    # comment the next 4 lines out after done creating the csv opp shell list files
    # creates the initial files if they don't exist
    for i, id in enumerate(config.available_resources.keys()):
        print(i,id)
        create_opp_lists(i,date1,date2,appserver_token)


    #################################################################
    # update entry_price for each opp
    #################################################################
    

    opp_list_filename = f'{config.opp_logs}opp_{resource_id}.csv'
    #---------
    df=pd.read_csv(opp_list_filename)
    
    for i,r in df.iterrows():
        print(f'{i}/{df.shape[0]}',end='\r')
        rdate = r['date']
        if rdate > today_date:
            break
        
        entry_price = r['entry_price']
        if entry_price != 0: continue  # this is already loaded

        r = get_stock_price_by_date(resource_id,r['symbol'],rdate)
        price = r['price']
        df.at[i,'entry_price']=price

    df.to_csv(opp_list_filename, index=False)

    #########################################################################################
    # add a column for each date and enter % gain or loss for each entry_price that's is > 0
    #########################################################################################

    t1 = time.time()



    opp_list_filename = f'{config.opp_logs}opp_{resource_id}.csv'
    df=pd.read_csv(opp_list_filename)

    diff = datetime.datetime.strptime(date2, '%Y-%m-%d') - datetime.datetime.strptime(date1, '%Y-%m-%d')
    num_days = diff.days

    # use AAPL stock to find what is the last date we have data downloaded.  could have used MSFT or anything to get the date
    # use the date to know when to stop processing
    last_date,last_price=get_last_price_date(0,'AAPL')

    for i in range(num_days+1):
        d = inc_date_day(date1,i)


        if d>last_date: # this is all the data we have for processing
            break 


        # date_gl = '2023-01-04'
        date_gl = d

        print('processing date:',d)

        # if column exists in the dataframe skip it - it's been processed
        if date_gl in df:
            continue

        df[date_gl]=0  # create a column with the date and set it to 0 initially

        # iterate the dataframe and set the % for the column 
        for i,r in df.iterrows():
            print(f'{i}/{df.shape[0]}     ',end='\r')
            rdate = r['date']

            if date_gl > r['date2'] : # column date is larger than date2
                continue

            if rdate > today_date:
                break

            entry_price = r['entry_price']
            if entry_price == 0:
                break

            if r['date'] >= date_gl:
                break    

            ######################### check if current date is in the opportunity date range ##################


            r = get_stock_price_by_date(resource_id,r['symbol'],date_gl)
            price = r['price']

            gl = (price - entry_price)/entry_price
            # print(i,price,entry_price,'gl=',gl)
            gl = round(gl,4)
            df.at[i,date_gl]=gl

        df.to_csv(opp_list_filename, index=False)
    # df.to_csv('test.csv', index=False)

    t2 = time.time()

    print('duration ',t2-t1,' seconds')
