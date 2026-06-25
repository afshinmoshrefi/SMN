from create_report import get_seasonal_chart_data, get_chart_data
from create_report import get_keyprovider_token, login_appserver 
import datetime
from datetime import timedelta
import requests
import sys
sys.path.insert(0, '/home/flask')
import config
#---------------------------------------------------------------------------------------------
def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')  
#---------------------------------------------------------------------------------------------
def get_cumulative_chart_data(barData):

    opp_dir = barData['stats']['Trade Dir']

    cumData = []
    cr=1

    for pp in barData['ChartData4']:
        p=pp['pct'].split(',')[0]
        p = float(p)
        if opp_dir == 'short': 
            cr *= (1+(-p/100))
        else                 : 
            cr *= (1+(p/100))                 
        cumData.append((cr*100)-100)

    return cumData
#---------------------------------------------------------------------------------------------
def get_chart_historical_prices(id,symbol,d0,d1):
    
    url = config.appserver_url+'/ChartHistorical2/'+str(id)+'/'+symbol+'/'+d0+'/'+d1+'?token='+appserver_token
    result = requests.get(url)
    price_data = result.json()
    return price_data
#---------------------------------------------------------------------------------------------
if __name__ == '__main__':

    financial_group_id = 10
    date1 = '2025-08-18'
    days_hold = '49' 
    symbol = 'CA30Y'
    years  = '8'
    
    # financial_group_id = 2
    # date1 = '2025-09-04'
    # days_hold = '30' 
    # symbol = 'WDAY'
    # years  = '10'

    zero_last_year = True
    base_year = str(datetime.datetime.now().year)
    category = config.category_report

    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)
    #---------------------------------------------------------------------------------------------
    # get trendchart data
    #---------------------------------------------------------------------------------------------
    labels,seaVals=get_seasonal_chart_data(financial_group_id,symbol,years,date1,appserver_token)

    # print('trendchart labels:')
    # print(labels)
    # print('trendchart values:')
    # print(seaVals)
    # exit()
    #---------------------------------------------------------------------------------------------
    # get barchart data
    #---------------------------------------------------------------------------------------------
    days_hold_corrected = str(int(days_hold)-1) # fixes the issue with days = today+hold_days
    cdata=get_chart_data(financial_group_id,date1,symbol,days_hold_corrected,years,zero_last_year,appserver_token)
    num_winning_years = int(cdata['stats']['Num Winners'])
    num_losing_years  = int(cdata['stats']['Num Losers'])
    success_text      = f'{num_winning_years} of {num_winning_years+num_losing_years}'

    # print('barchart data:')
    # print(cdata)
    # print('num_winning_years=',num_winning_years)
    # print('num_losing_years=',num_losing_years)
    # print('success_text=',success_text)
    # exit()
    #---------------------------------------------------------------------------------------------
    # get cumulative return data
    #---------------------------------------------------------------------------------------------
    cum_data=get_cumulative_chart_data(cdata)
    # print('cumulative chart data:',cum_data)
    # exit()
    #---------------------------------------------------------------------------------------------
    # get price data
    #---------------------------------------------------------------------------------------------
    # print('price data')
    start_days_prior = 60
    d1 = date1
    d0 = inc_date_day(date1,-start_days_prior)  # chart of 60 days ago to current
    print(d0, d1)
    price_data=get_chart_historical_prices(financial_group_id,symbol,d0,d1)
    print(price_data)