
# this app generates 24 images based on the top 10 opp lists for each of the 12 financial groups
# it creates both a desktop and a mobile version of each of the images and place them in 
# png_path_m = config.chart_root_folder+'img/top10/'+top10_urls[k]+today_date+'-m.png'#
# png_path_d = config.chart_root_folder+'img/top10/'+top10_urls[k]+today_date+'-d.png'#



import imgkit
import os
import sys
import time
import datetime
sys.path.insert(0, '/home/flask')
import config


# create images of the top10 both mobile and desktop - should run daily

# list of url segments to use 

options_d  = {'crop-h': '285','crop-y': '310','width': '900'}
options_m1 = {'crop-h': '290','crop-y': '325','width': '420'}
options_m2 = {'crop-h': '290','crop-y': '360','width': '420'}
today_date  = datetime.datetime.now().strftime("%Y-%m-%d")
d = config.domain_root
if not os.path.exists(config.chart_root_folder+'img/top10/'): os.makedirs(config.chart_root_folder+'img/top10/')
for k in config.top10_urls.keys():
    url = d+config.top10_urls[k]+today_date # url of the web page we're gonna take a screenshot from
    # create 2 images one for desktop and 1 mobile for each of the top10 list
    png_path_m = config.chart_root_folder+'img/top10/'+config.top10_urls[k]+today_date+'-m.png'
    png_path_d = config.chart_root_folder+'img/top10/'+config.top10_urls[k]+today_date+'-d.png'

    # generate the images
    if k == 'FUTURES & COMMODITIES' or k == 'RUSSELL 1000 STOCKS' or k == 'GOVERNMENT BONDS' or k == 'NASDAQ 100 STOCKS':
        imgkit.from_url(url,png_path_m, options=options_m2)
    else:
        imgkit.from_url(url,png_path_m, options=options_m1)
    imgkit.from_url(url,png_path_d, options=options_d)
    
    

    
    