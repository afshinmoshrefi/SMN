# This script generates a video that can be posted on youtube, facebook, tiktok, instagram 

# this version only creates the selection list csvs in the folder /logs/daily_video_selection_lists
# if this file is not found, generate_blog_video.py creates it - this one creates multiple future versions for 
# reviewing and studying what selections are coming up othewise is not necessary for the opration of automated videos

import json
import requests
import pandas as pd
import pprint
import os.path
from os import listdir
import time
import datetime
from datetime import timedelta
import jwt
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.patches import Rectangle
import calendar
import random
import base64
import textwrap # removes indents in multi line strings
import sys
import random
from blog_tools import convert_param_base64,json_log,inc_date_day,diff_between_dates,get_keyprovider_token, login_appserver,create_title_slug
from PIL import Image, ImageDraw, ImageFont
from thumbnails import create_socialmedia_thumbnail 
from get_top10_data import load_top10,get_chart_data
from slugify import slugify


# from video_images_mp3s import create_video_from_png_mp3
from video_images_mp4_mp3s import create_video_from_png_mp4_mp3


from google_text_to_speech import text_to_mp3
from get_top_high_num_years_opps import load_top_20year # this one find the opps that are 20/20 or 19/19 at 100% searches to 10/10 and returns 1 dataframe

import video_post_template
from create_report import generate_report

sys.path.insert(0, '/home/flask')
import config




global_font = '/home/flask/blog/images/font/Roboto-Bold.ttf'

video_size = {
    # 'fb'                    :(1200,630),
    'facebook'              :(1280,720),
    # 'twitter'               :(1600,900),
    # 'twitter_post'          :(1600,900),
    # 'twitter_recommended1'  :(1080,1080),
    # 'twitter_recommended2'  :(1080,1350),
    # 'instagram'             :(1080,1080),
    # 'linkedin'              :(1200,627),
    # 'pinterest'             :(1000,1500),
    'youtube'               :(1280,720),
    # 'tiktok'                :(1080,1920),
    # 'tiktok3'               :(1080,640)
}

# (font_size,padding_left,padding_right,padding_top,padding_bottom,font_size_title)
img_settings = {
    # 'fb'                    :(60,0.02,0.02,0.04,0.04),
    'facebook'              :(60,0.02,0.02,0.04,0.04,70),
    'twitter'               :(75,0.02,0.02,0.04,0.04,80),
    'twitter_post'          :(60,0.02,0.02,0.04,0.04,80),
    'twitter_recommended1'  :(60,0.02,0.02,0.04,0.04,80),
    'twitter_recommended2'  :(60,0.02,0.02,0.04,0.04,80),
    'instagram'             :(60,0.02,0.02,0.14,0.14,80),
    'linkedin'              :(60,0.02,0.02,0.04,0.0,804),
    'pinterest'             :(60,0.02,0.02,0.2,0.2,80),
    'youtube'               :(60,0.02,0.02,0.04,0.04,70),
    'tiktok'                :(60,0.02,0.02,0.04,0.04,80),
    'tiktok3'               :(60,0.02,0.02,0.04,0.04,80),
}
#------------------------------------------------------------------------------------------------
def format_daterange_text(date1, date2):
    # Convert the dates to datetime objects
    d1 = datetime.datetime.strptime(date1, '%Y-%m-%d')
    d2 = datetime.datetime.strptime(date2, '%Y-%m-%d')
    
    # Get the month abbreviations and dates
    month1 = d1.strftime("%B")
    day1 = str(int(d1.strftime("%d")))
    month2 = d2.strftime("%B")
    day2 = str(int(d2.strftime("%d")))
    
    # Concatenate the month abbreviations and dates to form the final string
    result = month1 + ' ' + day1 + "-" + month2 +' '+ day2

    return result
#------------------------------------------------------------------------------------------------
# image_format is of one of the social networks like 'facebook'  
# table_data is the format of the table, like:
# example table_data:
# table_data = [
#     ("Symbol", "AMSF"),
#     ("Trade Direction", "Long"),
#     ("Date Range", "Sep18-Feb19"),
#     ("Days Hold", "155"),
#     ("History Years", "10"),
#     ("Securities Group", "WILSHIRE 5000"),
# ]
#file_path is the filelocation and type to save the created image
def create_image_of_table(image_format,table_data,file_path):

    table_width = video_size[image_format][0]  # YouTube dimensions
    table_height = video_size[image_format][1]   # YouTube dimensions

    padding_left = img_settings[image_format][1]
    padding_right = img_settings[image_format][2]
    padding_top =  img_settings[image_format][3]
    padding_bottom =  img_settings[image_format][4]
    row_height = (table_height - padding_top - padding_bottom) / len(table_data)
    font_size_table = img_settings[image_format][0]
    # font_size_title = img_settings[image_format][5]
    font = ImageFont.truetype(global_font, font_size_table)
    # font_title = ImageFont.truetype(global_font, font_size_table)
    text_color = (0, 0, 0)  # Black
    text_color_sym = (255,0,0)
    line_color = (0, 0, 0)  # Black
    output_image_path = "table.png"

    # Create a new image with padding
    image = Image.new("RGB", (table_width, table_height), "white")
    draw = ImageDraw.Draw(image)

    # get the text height from the first line for vertical placement
    _,_,text_width, text_height = draw.textbbox((0,0),table_data[0][0], font=font)

    # draw a rectangle - this was to visualize the padding during development 
    # this is the coordinates of the padding rectangle
    top_left_x     = int(table_width * padding_left)
    top_left_y     = int(table_height * padding_top)
    bottom_right_x = int(table_width * (1-padding_right))
    bottom_right_y = int(table_height * (1-padding_bottom))
    # left_top  = (top_left_x,top_left_y)
    # right_bottom = (bottom_right_x, bottom_right_y)
    # outline_color = (255, 0, 0)  # Red in RGB
    # draw.rectangle([left_top, right_bottom], outline=outline_color)

    num_rows = len(table_data)
    row_height = int((bottom_right_y - top_left_y)/num_rows)

    # # Draw the table
    # this shift is based on calculated text_height and current row_hiehgt
    shift_down=(row_height - text_height)/row_height/2

    for r in range(num_rows):
        
        draw.text((top_left_x,  top_left_y+ (r+shift_down)* row_height), table_data[r][0], fill=text_color, font=font)
    #     # Draw right-aligned text
        # text_width, _ = draw.textsize(table_data[r][1], font=font)  #deprecated
        _,_,text_width, _ = draw.textbbox((0,0),table_data[r][1], font=font)

        text_color_right = text_color
        if r==0 and table_data[r][0] == 'Symbol':
            text_color_right = text_color_sym # making ticker symbol red
        draw.text((bottom_right_x - text_width , top_left_y+ (r+shift_down)* row_height), table_data[r][1], fill=text_color_right, font=font)
    #     # Draw a line below each row
        draw.line([(top_left_x, top_left_y+ (r+1)* row_height), (bottom_right_x,top_left_y+ (r+1)* row_height)], fill=line_color)

    # Save the image
    image.save(file_path)
#----------------------------------------------------------------------------------------------------------------------
# this function creates the 3 tables on the reports individualy as png based on the social media resolution passed
#----------------------------------------------------------------------------------------------------------------------
def get_3_stats_tables_png(opp_dict,resourceID,social_type): # social_type is like youtube or facebook ....


    # for k in opp_dict:
    #     print(k,type(opp_dict[k]))
    
    # exit()


    ########################################################
    # SCREW UP REPORT - MAKE A DIFFERENT IN AVERAGE PROFIT
    # THERE are 2 average profits in the dictionary
    # avg_profit is based on winning+losing years
    # Avg Profit is based on winning years only
    ########################################################


    # table1

    daterange = format_daterange_text(opp_dict['Date'], opp_dict['Date2'])
    # get years from post title
    s=opp_dict['post_title'].split('-')
    years = s[0]
    security_group_name = config.available_resources[str(resourceID)]

    # pprint.pprint(opp_dict)
    # create 3 images for tables shown on the report
    table_data1 = [
        ("Symbol", opp_dict['Symbol']),
        ("Trade Direction", opp_dict['Direction']),
        ("Date Range", daterange),
        ("Days Hold", str(opp_dict['DaysOut'])),
        ("History Years", str(years)),
        ("Securities Group", security_group_name),
    ]
    table_data2 = [
        ("Num Winners", str(opp_dict['num_winners'])),
        ("Num Losers", str(opp_dict['num_losers'])),
        ("Percent Profitable", opp_dict['pct_profitable']),
        ("Avg Profit", str(opp_dict['Avg Profit'])),
        ("Avg Loss", opp_dict['avg_loss']),
        ("Biggest Winner", opp_dict['biggest_winner']),
    ]
    table_data3 = [
        ("Median Profit", str(opp_dict['median_profit'])),
        ("Std Dev", opp_dict['stddev']),
        ("Cumulative Return", str(opp_dict['cumulative_return'])),
        ("Sharpe Ratio", str(opp_dict['Sharpe Ratio'])),
        ("Trend Long", str(opp_dict['Trend Long'])),
        ("Trend Short", str(opp_dict['Trend Short'])),
    ]

    print('table3=',table_data3)

    # check if tmp folder exists - if not create it
    if not os.path.exists(config.tmp_dir):os.makedirs(config.tmp_dir)
    create_image_of_table(social_type,table_data1,config.tmp_dir+'table1.png')
    create_image_of_table(social_type,table_data2,config.tmp_dir+'table2.png')
    create_image_of_table(social_type,table_data3,config.tmp_dir+'table3.png')

    # exit()

    return config.tmp_dir+'table1.png',config.tmp_dir+'table2.png',config.tmp_dir+'table3.png'
#------------------------------------------------------------------------------------------------
# first generate the text for narration
# 1) - opening text - display the thumbnail or display the text ?
# 2) - trade detail text - display table 1
# 3) - barchart text - display the barchart
# 4) - gainloss1 text - display table 2
# 5) - info text - display table 3
# 6) - cumulative text - display cumulative chart
# 7) - trend chart - display trend chart
# 8) - closing text + CTA - display text & CTA

def generate_text(d,resourceID,social_type,debug_idx):
    # pprint.pprint(d) 
     

    security_group_name = config.available_resources[str(resourceID)].replace('STOCKS','Stocks')
    daterange = format_daterange_text(d['Date'], d['Date2']).replace('-',' and ')
    dr1 = daterange.split(' and ')[0]
    dr2 = daterange.split(' and ')[1]
    years = d['post_title'].split('-')[0]

    symbol = ' '.join(d['Symbol'])
    company = d['company'].replace('&','an')

    num_winners=d['num_winners']
    num_losers=d['num_losers']
    pct_profitable=d['pct_profitable']
    direction=d['Direction']
    avg_profit=d['Avg Profit']
    avg_loss=d['avg_loss']
    biggest_winner=d['biggest_winner']
    stddev=d['stddev']
    cumulative_return=d['cumulative_return']
    median_profit=d['median_profit']
    daysOut=d['DaysOut']

    strength = 'neutral'
    bullish_bearish_neutral = ''

    l=d['Trend Long']
    s=d['Trend Short'] 
    if l>s  :  bullish_bearish_neutral = 'bullish'
    if l<s  :  bullish_bearish_neutral = 'bearish'

    if l>80 or s>80:strength = 'strong'
    if (l>50 and l<90) or  (s>50 and s<90): strength = 'mild'

    s_losers = '' # this variable should be set to s_losers='s'  if there are more than 1 loser in thsi opportunity
    if int(num_losers) > 1 :
        s_losers = 's'

    vars_dict = {
        'security_group_name': security_group_name,
        'daterange': daterange,
        'dr1': dr1,
        'dr2': dr2,
        'years': years,
        'symbol': symbol,
        'company': company,
        'num_winners': num_winners,
        'num_losers': num_losers,
        'pct_profitable': pct_profitable,
        'direction': direction,
        'avg_profit': avg_profit,
        'avg_loss': avg_loss,
        'biggest_winner': biggest_winner,
        'stddev': stddev,
        'cumulative_return': cumulative_return,
        'median_profit': median_profit,
        'daysOut': daysOut,
        'strength': strength,
        'bullish_bearish_neutral': bullish_bearish_neutral,
        's_losers': s_losers
    }

    # pprint.pprint(vars_dict)
    # exit()

    # print(direction)


    # t1 = f"""The TradeWave report for {company} with the ticker symbol of {symbol}, shows an opportunity between {daterange}. This video provides an in-depth analysis of the date range, highlighting key TradeWave patterns in the price movements of {symbol} during this period, along with supporting historical data and charts. This information can be used by investors and traders to study potential trading opportunities, manage risk, and make informed trading decisions."""

    # if direction == 'Long':
    #     t2 = f"""{company} is a member of {security_group_name}.  The daterange strategy for {symbol} is a {direction} opportunity when bought on {dr1},  held for {daysOut} calendar days and sold on {dr2}.  This report has been compiled with {years} years of history."""
    # else :
    #     t2 = f"""{company} is a member of {security_group_name}.  The daterange strategy for {symbol} is a {direction} opportunity when sold on {dr1}  held for {daysOut} calendar days and bought on {dr2}.  This report has been compiled with {years} years of history."""
    
    # if direction == 'Long':
    #     t3 = f"""The bar chart for this opportunity is showing the percent gain of {symbol} when bought on {dr1} and sold on {dr2} for each of the past {years} years. """
    # else:
    #     t3 = f"""The bar chart for this opportunity is showing the percent gain of {symbol} when sold on {dr1} and bought on {dr2} for each of the past {years} years. """

    # if pct_profitable == '100%':
    #     t4 = f"""The current table shows details of the performance of the date range with {num_winners} winners and no losers in the past {years} years; this date range was profitable {pct_profitable} of the last {years} years  with average profit of {avg_profit}%.  The largest gain was for {biggest_winner} gain."""
    # else:
    #     t4 = f"""The current table shows details of the performance of the date range with {num_winners} winners and {num_losers} loser in the past {years} years; this date range was profitable {pct_profitable} of the last {years} years with average profit of {avg_profit}% and for losing year an average loss of {avg_loss}.  The largest winner was for {biggest_winner} gain."""

    # t5 = f"""The median profit for this date range was {median_profit}% with standard deviation of {stddev}; Cumulative Return for the historical years was {cumulative_return}%. Trend Long and Trend Short show {strength} {bullish_bearish_neutral} trend for the recent past. """

    # t6 = f""" The cumulative chart shows the annual historical capital growth of the strategy for each of the past {years} years.  the cumulative growth at the end of {years} years was {cumulative_return}%."""

    # t7 = f""" The trend chart shows the {years} year average performance of {symbol} through out the year.  When studying this chart you can observe sharp uptrends and downtrends indicating potential historical timing for a wave opportunity.  Use this chart to see the overall strength of {symbol} through out the year."""

    # t8 = """With Trade Wave you can observe and research performance of all securities based on the time of the year.  Visit Trade Wave dot AI and see how we make data driven securities research easy for everyone."""

    debug = False 

    # debugging turns off random and picks exactly the debug_idx that was passed
    # in case its >4 then it goes back to 0 for the items that are only 5
    if debug_idx > -1:
        debug = True
        t1_idx = debug_idx
        d_idx = t1_idx
        if d_idx>4:d_idx=d_idx-5 # because we have 10 t1 and 5 of the rest - this is only relevant for debug

    #----------------------------------------------------------------------------------
    t1 = random.choice(video_post_template.t1)
    if debug: t1=video_post_template.t1[t1_idx]
    t1 = t1.format(**vars_dict)
    #----------------------------------------------------------------------------------
    t1_disclaimer = video_post_template.t1_disclaimer[0] # only 1 version so no random pick
    t1_disclaimer = t1_disclaimer.format(**vars_dict)
    #----------------------------------------------------------------------------------
    if direction == 'Long':
        t2 = random.choice(video_post_template.t2_long)
        if debug: t2=video_post_template.t2_long[d_idx]
    else:
        t2 = random.choice(video_post_template.t2_short)
        if debug: t2=video_post_template.t2_short[d_idx]
    t2 = t2.format(**vars_dict)
    #----------------------------------------------------------------------------------
    if direction == 'Long':
        t3 = random.choice(video_post_template.t3_long)
        if debug: t3=video_post_template.t3_long[d_idx]
    else:
        t3 = random.choice(video_post_template.t3_short)
        if debug: t3=video_post_template.t3_short[d_idx]
    t3 = t3.format(**vars_dict)
    #----------------------------------------------------------------------------------
    if pct_profitable == '100%':
        t4 = random.choice(video_post_template.t4_100)
        if debug: t4=video_post_template.t4_100[d_idx]
    else:
        t4 = random.choice(video_post_template.t4_less_100)
        if debug: t4=video_post_template.t4_less_100[d_idx]
    t4 = t4.format(**vars_dict)
    #----------------------------------------------------------------------------------
    t5 = random.choice(video_post_template.t5)
    if debug: t5=video_post_template.t5[d_idx]
    t5 = t5.format(**vars_dict)
    #----------------------------------------------------------------------------------
    t6 = random.choice(video_post_template.t6)
    if debug: t6=video_post_template.t6[d_idx]
    t6 = t6.format(**vars_dict)
    #----------------------------------------------------------------------------------
    t7 = random.choice(video_post_template.t7)
    if debug: t7=video_post_template.t7[d_idx]
    t7 = t7.format(**vars_dict)
    #----------------------------------------------------------------------------------
    if social_type == 'youtube':
        t8 = random.choice(video_post_template.t8_youtube)
        if debug: t8=video_post_template.t8_youtube[d_idx]
    elif social_type == 'facebook':
        t8 = random.choice(video_post_template.t8_facebook)
        if debug: t8=video_post_template.t8_facebook[d_idx]
    else:
        t8 = random.choice(video_post_template.t8)
        if debug: t8=video_post_template.t8[d_idx]
    t8 = t8 + video_post_template.t9_cta[0]
    t8 = t8.format(**vars_dict)
    #----------------------------------------------------------------------------------
    

    
    



    text_dict = {
        # 't1':t1.replace('14','fourteen').replace('20','twenty'),
        't1':t1.replace(' 20 ',' twenty ').replace(' 19 ','ninteen').replace(' 15 ',' fifteen ').replace(' 14 ', ' fourteen ').replace(' 13 ',' thirteen ').replace(' 12 ',' tweleve ').replace(' 11 ',' eleven ').replace(' 10 ',' ten '),
        't1_disclaimer':t1_disclaimer,
        't2':t2,
        't3':t3,
        't4':t4,
        't5':t5,
        't6':t6,
        't7':t7,
        't8':t8
        } 

    # put a small delay around each of the text because of that poping sound during transitions

    for t in text_dict:
        text_dict[t] = '<break time="600ms"/>' + text_dict[t] + '<break time="600ms"/>' 
        
    pprint.pprint(text_dict)
    # exit()

    return text_dict
#------------------------------------------------------------------------------------------------
def generate_mp3_from_text(text_dict):

    for t in text_dict: 
        text_dict[t] = text_dict[t].replace('S&P 500','s-an-p-500').replace('FUTURES & COMMODITIES','FUTURES and COMMODITIES')

    audio_dict = {}
    if not os.path.exists(config.tmp_dir):os.makedirs(config.tmp_dir)
    # delay = '<break time="400ms"/>'
    for k in text_dict:
        text_dict[k] = f'<speak>'+text_dict[k]+f'</speak>'
        # print(k)
        # print(text_dict[k])
        mp3_file_path = config.tmp_dir+k+'.mp3'
        text_to_mp3(text_dict[k],config.tmp_dir+k+'.mp3')
        audio_dict[k] = mp3_file_path


    return audio_dict
#------------------------------------------------------------------------------------------------
def get_video_assets(opp_dict,social_type,debug_idx):



    years = opp_dict['post_title'].split('-')[0] # get years from the title of the opp_report
    title_pre = ''
    category = config.category_report

    resourceID = opp_dict['resourceID']

    table_img_path1,table_img_path2,table_img_path3=get_3_stats_tables_png(opp_dict,resourceID,social_type)
    # add the path to the table images to this dictionary
    opp_dict['table1_img'] = table_img_path1
    opp_dict['table1_img'] = table_img_path2
    opp_dict['table1_img'] = table_img_path3

    # generate text 
    text_dict=generate_text(opp_dict,resourceID,social_type,debug_idx)

    # pprint.pprint(text_dict)
    # for t in text_dict:
    #     print(t)
    #     print(text_dict[t])
    #     print('')


    audio_dict = generate_mp3_from_text(text_dict)




    # print(text_dict)
    # print(audio_dict)


    # create a thumbnail for this opportunity to be used either as main thumbnail on youtube or as a featured image for other social media
    thumbnail_path,thumbnail_url = create_socialmedia_thumbnail(social_type,resourceID,opp_dict['Date'],opp_dict['Symbol'],opp_dict['DaysOut'],opp_dict['Direction'],opp_dict['Avg Profit'],years,title_pre,category)
    opp_dict['thumbnail_path'] = thumbnail_path
    opp_dict['thumbnail_url'] = thumbnail_url


    


    return opp_dict,text_dict,audio_dict
#-------------------------------------------------------------------------------------------------------
# resize the chart image 1400 x 600 to fit in youtube 1200 x 700 - exxtra white space on top and bottom
# also put a title on the top
#-------------------------------------------------------------------------------------------------------
def resize_png(input_png,output_png,title):

    font_size_title = img_settings['youtube'][5]
    font_title = ImageFont.truetype(global_font, font_size_title)

    new_width = 1280
    new_height = 720
    # aspect_ratio = new_width/new_height

    

    img = Image.open(input_png)
    print(img.size)
    chart_resize_facor = 0.95
    resized_img_width  = int(new_width * chart_resize_facor)
    resized_img_height = int(resized_img_width * img.size[1]/img.size[0])

    resized_img = img.resize((resized_img_width,resized_img_height),Image.Resampling.LANCZOS)



    resized_final_img = Image.new("RGB", (new_width, new_height), "white") # this is the right size we want
    # paste the resize image to this one now justify to the bottom
    chart_x1 = int((new_width - resized_img_width)/2)
    chart_y1 = int((new_height-resized_img_height)/2)
    resized_final_img.paste(resized_img,(chart_x1,chart_y1))


    border_width = 1
    x1=chart_x1-border_width
    y1=chart_y1-border_width
    x2=chart_x1+resized_img_width+border_width
    y2=chart_y1+resized_img_height+border_width

    draw = ImageDraw.Draw(resized_final_img)
    draw.rectangle([x1, y1, x2, y2], outline="black", width=border_width)

    title_color = (60,60,60)
    _,_,text_width, text_height = draw.textbbox((0,0),title, font=font_title) # get width of the title for centering
    x = int((new_width - text_width)/2)
    y = int((y1-text_height)/2)-5
    # print(new_width,text_width)
    # exit()
    draw.text((x , y), title, fill=title_color, font=font_title)


    print(resized_img_width,resized_img_height) 

    
    resized_final_img.save(output_png)

    # print(output_png)

    # exit()

#-------------------------------------------------------------------------------------------------
# if debug index is anywhere from 0 to 9 - the random is turned off and that index text is picked
#-------------------------------------------------------------------------------------------------
def generate_blog_video(d,category,social_network,video_filepath,debug_idx = -1):

    resourceID=d['resourceID']
    company=d['company']
    symbol=d['Symbol']
    date1=d['Date']
    date2=d['Date2']
    daysOut=d['DaysOut']
    years=str(d['years'])

  
    #-----------------------------------------------------------------------------
    # first step is to generate a date-range report for the opportunity used for
    # chart images + reference blog accessed through video description
    #-----------------------------------------------------------------------------
    base_year = date1[:4]
    keyprovider_token = get_keyprovider_token()
    appserver_token   = login_appserver(keyprovider_token)
    post_id,msg,post_slug=generate_report(appserver_token,resourceID,date1,daysOut,symbol,years,True,base_year,category)
    post_title,post_slug2=create_title_slug(company,symbol,date1,date2,years,category)
    d['post_title']=post_title
    
    # get location of the 3 report images
    # root_index = len(config.domain_root) # this is to remove root from the beginning
    subfolders = f"{base_year}/{d['Date']}/"
    bar_img = config.chart_root_folder+subfolders+'gain-loss-barchart-'+post_slug+'.png'
    cum_img = config.chart_root_folder+subfolders+'cumulative-return-'+post_slug+'.png'
    sea_img = config.chart_root_folder+subfolders+'trend-chart-'+post_slug+'.png'

    # print(bar_img)
    # print(cum_img)
    # print(sea_img)
    
    # resize bar_img cum_img and sea_img
    png_bi = config.tmp_dir+'bar_img.png'
    png_ci = config.tmp_dir+'cum_img.png'
    png_si = config.tmp_dir+'sea_img.png'

    resize_png(bar_img,png_bi,'Profit Bar Chart')
    resize_png(cum_img,png_ci,'Cumulative Chart')
    resize_png(sea_img,png_si,'Trend Chart')

    # exit()

    d['bar_img'] = png_bi
    d['cum_img'] = png_ci
    d['sea_img'] = png_si

    opp_dict,text_dict,audio_dict=get_video_assets(d,social_network,debug_idx)
    pprint.pprint(opp_dict)


    data = [
        (opp_dict['thumbnail_path']       ,'/home/flask/blog/tmp/t1.mp3'),
        (config.tmp_dir+'Disclaimer Slide 16-9 YT.png' ,'/home/flask/blog/tmp/t1_disclaimer.mp3'),
        (config.tmp_dir+'table1.png','/home/flask/blog/tmp/t2.mp3'),
        (opp_dict['bar_img']              ,'/home/flask/blog/tmp/t3.mp3'),
        (config.tmp_dir+'table2.png','/home/flask/blog/tmp/t4.mp3'),
        (config.tmp_dir+'table3.png','/home/flask/blog/tmp/t5.mp3'),
        (opp_dict['cum_img']              ,'/home/flask/blog/tmp/t6.mp3'),
        (opp_dict['sea_img']              ,'/home/flask/blog/tmp/t7.mp3'),
        # (opp_dict['thumbnail_path']       ,'/home/flask/blog/tmp/t8.mp3'),
        (f'/home/flask/blog/tmp/final_clip_{social_network}_automated_videos.mp4'   ,'/home/flask/blog/tmp/t8.mp3'),
    ]


    # pprint.pprint(data)
    # exit()

    # final_clip=video_file_path=create_video_from_png_mp3(data)
    final_clip=video_file_path=create_video_from_png_mp4_mp3(data)
    final_clip.write_videofile(video_filepath, fps=24)
#------------------------------------------------------------------------------------------------
# get opportuniteis for generateing videos in youtube and facebook - return as dictionaries    
#------------------------------------------------------------------------------------------------
def get_facebook_youtube_opp(opp_date):


    # check if there are video records for opp_date - if there are, then skip this - videos have already been created
    dict_list = json_log(config.video_creation_log,'get',{})
    if dict_list != None:
        opp_date_records_list = [entry for entry in dict_list if entry["Date"] == opp_date]

        if len(opp_date_records_list) > 0: return {}



    look_back_num_days = 7
    num_videos_daily = 2 # this is the number for long and the same number for shorter term opps - makes a total of 4 videos

    # check if the filtered opportunity csv file already exists, load and return that otherwise recreate it
    filtered_csv = f'{config.video_opp_logs_by_date}filtered_list_{opp_date}.csv'
    if os.path.exists(filtered_csv):
        dfd = pd.read_csv(filtered_csv)
        dfd = dfd.sort_values('cumulative_divided_by_year',ascending=False).reset_index(drop=True)
        dfd = dfd.loc[:, ~dfd.columns.str.contains('Unnamed')]
    else:
        
        # get the list of opportunities for 20 years     
        filename = config.top_20year_data
        action,dfc=load_top_20year (filename,opp_date)

        # if there are duplicate rows with the same symbol.  only keep the row with the highest # of years 
        dfc = dfc.sort_values('years').drop_duplicates('Symbol', keep='last')

        dfc['mp'] = dfc['median_profit'].str.rstrip('%').astype(float) # create a float median profit column - source is string
        dfc['days'] = dfc['DaysOut'].astype(int) # integer version of daysOut
        dfc ['profit_per_day'] = dfc['avg_profit']/dfc['days']


        dfc['cr'] = dfc['cumulative_return'].str.rstrip('%').astype(float) # create a float median profit column - source is string
        dfc['cumulative_divided_by_year'] = dfc['cr']/dfc['years']


        # print(dfc.dtypes)
        # print(dfc)
        
        condition1 = ( (dfc['mp'] >= 7.0) & (dfc['Sharpe Ratio'] >= 1.0) & (dfc['years'] >  35)  & (dfc['years'] <= 40) )
        condition2 = ( (dfc['mp'] >= 7.5) & (dfc['Sharpe Ratio'] >= 1.0) & (dfc['years'] >  30)  & (dfc['years'] <= 35) )
        condition3 = ( (dfc['mp'] >= 8.0) & (dfc['Sharpe Ratio'] >= 1.0) & (dfc['years'] >  25)  & (dfc['years'] <= 30) )
        condition4 = ( (dfc['mp'] >= 8.5) & (dfc['Sharpe Ratio'] >= 1.0) & (dfc['years'] >  20)  & (dfc['years'] <= 25) )
        condition5 = ( (dfc['mp'] >= 9.0) & (dfc['Sharpe Ratio'] >= 1.0) & (dfc['years'] >  15)  & (dfc['years'] <= 20) )
        condition6 = ( (dfc['mp'] >= 9.0) & (dfc['Sharpe Ratio'] >= 1.1) & (dfc['years'] >  12)  & (dfc['years'] <= 15) )
        condition7 = ( (dfc['mp'] >=10.0) & (dfc['Sharpe Ratio'] >= 1.2) & (dfc['years'] >= 10)  & (dfc['years'] <= 12) )

        dfd = dfc[condition1 | condition2 | condition3 | condition4 | condition5 | condition6 | condition7].reset_index(drop=True)

        dfd = dfd.sort_values('cumulative_divided_by_year',ascending=False).reset_index(drop=True)


        # if dfd.shape[0] > 15:
        #     dfd = dfd[dfd['days']<150].reset_index(drop=True)


        print(dfd.shape[0])

        print(dfd)

        # Filter out the unnamed columns using boolean indexing
        dfd = dfd.loc[:, ~dfd.columns.str.contains('Unnamed')]
        os.makedirs(config.video_opp_logs_by_date, exist_ok=True) 
        dfd.to_csv(filtered_csv) # these files are saved for long term use and debugging - to recreate it must be deleted first

        # dfd is used as the list of opportuniteis for selection for videos
    
    print(dfd)
    
    ##################################################################################################################
    # dfd is the list of opportunities - pick by random - don't have the same opp for youtube and facebook
    ##################################################################################################################
    # there are duplicated columns in dfd - its just different types - fixing another screwup
    # mp=median_profit  days=DaysOut  avg_profit = Avg Profit
    # video_creation_log  # use json_log(log_filename,action,dict)  action 'add'  'get'

    avoid_ticker_list = []

    # print(dfd)
    ten_days_before_opp_date = inc_date_day(opp_date, -look_back_num_days)
    # print(ten_days_before_opp_date)
    # exit()

    dict_list = json_log(config.video_creation_log,'get',{})
    if dict_list != None:
        filtered_list = [entry for entry in dict_list if entry["Date"] >= ten_days_before_opp_date]



        avoid_ticker_list = [entry["Symbol"] for entry in filtered_list]

    print('avoid_ticker_list_length=',len(avoid_ticker_list))
    print('avoid_ticker_list=',avoid_ticker_list)



    # get the list of tickers for posted videos for each of youtube and facebook for past 10 days so that we don't repeat
    # also don't put the same ticker opp for youtube and facebook
    # create a list of tickers to avoid because its been used in the last 10 days
    
    
    if dict_list != None:
        pass
    # get all the posts on facebook and youtube for the last 10 days and add them to avoid_ticker_list

    # divide dfd to dfd_g_140 and dfd_l_140
    dfd_g_140 = dfd[dfd['days']>=140].reset_index(drop=True)
    dfd_l_140 = dfd[dfd['days']< 140].reset_index(drop=True)
    # scan from top of dfd down sorted by cumulative_divided_by_year column and pick opportunities for youtube and facebook
    g_picks=[]
    for i,r in dfd_g_140.iterrows():
        symbol = r['Symbol']
        if symbol in avoid_ticker_list:
            continue
        g_picks.append(i)
        # save in config.video_creation_log
        dict_tmp = r.to_dict()
        json_log(config.video_creation_log,'add',dict_tmp)
        if len(g_picks) == num_videos_daily: # initially 2 perday for greater than 140 day opportunities
            break # we need maximum of 2 picks
    #----------------
    l_picks=[]
    for i,r in dfd_l_140.iterrows():
        symbol = r['Symbol']
        if symbol in avoid_ticker_list:
            continue
        l_picks.append(i)
        # save in config.video_creation_log
        dict_tmp = r.to_dict()
        json_log(config.video_creation_log,'add',dict_tmp)
        if len(l_picks) == num_videos_daily:  # initially 2 perday for less than 140 day opportunities
            break # we need maximum of 2 picks

    print('g_picks=',g_picks)
    print('l_picks=',l_picks)

    # create a return dictionary containing up to 4 picks

    ret_dict = {}

    picked_g_symbols=[]
    picked_l_symbols=[]

    for idx, value in enumerate(g_picks):
        ret_dict[f'g140_{idx}']=dfd_g_140.iloc[value].to_dict()
        picked_g_symbols.append (dfd_g_140['Symbol'].iloc[value])

    for idx, value in enumerate(l_picks):
        ret_dict[f'l140_{idx}']=dfd_l_140.iloc[value].to_dict()
        picked_l_symbols.append (dfd_l_140['Symbol'].iloc[value])

    print('picked_g_symbols=',picked_g_symbols)
    print('picked_l_symbols=',picked_l_symbols)
    
    return ret_dict
 
#------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    # facebook_hashtags = '#TradingOpportunities #FinancialMarkets #Top10Reports #FreeOfCharge #ProfitableTrading #JoinUsNow #traders #investors #Stocks #ETF #Forex #Futures #forex #forextrader #ForexMarket #futurestrading #bonds #seasonal #StockMarketSeasonality #SeasonalPatterns #StockTrading #InvestingTips #FinancialAnalyst #StockPicking #TradeLikeAPro #StockMarketEducation #InvestorLife #MarketAnalysis'


    today_date  = datetime.datetime.now().strftime("%Y-%m-%d")

    
    
   

    # remove all % values at the end of values of the dictionary
    # d = {key: (value[:-1] if isinstance(value, str) and value.endswith('%') else value) for key, value in d.items()}

    ####################################################
    ####################################################
    category       = config.category_date_range_report # used to create blog report with this category and use it with video





    # video_filepath = 'tttttt.mp4'
    # social_network = 'youtube'
    # # image_resolution_type = 'youtube'
    # num_on_list = 3 # 162
    # debug_idx = 3
    # d = dfd.iloc[num_on_list].to_dict()
    # generate_blog_video(d,category,social_network,video_filepath,debug_idx)

    # exit()

    # opp_date = today_date
    # opp_date = '2023-09-27'

    # d_facebook,d_youtube = get_facebook_youtube_opp(opp_date)



    



    # # this creates a sequence
    # date1 = '2023-10-16'
    # date2 = '2023-11-06'
    # diff = datetime.datetime.strptime(date2, '%Y-%m-%d') - datetime.datetime.strptime(date1, '%Y-%m-%d')
    # num_days = diff.days

    # for i in range(num_days+1):
    #     d = inc_date_day(date2,-i)
    #     print(d)
    #     d_facebook,d_youtube = get_facebook_youtube_opp(d)




    date1 = '2023-10-01'
    date2 = '2023-11-11'
    diff = datetime.datetime.strptime(date2, '%Y-%m-%d') - datetime.datetime.strptime(date1, '%Y-%m-%d')
    num_days = diff.days

    for i in range(num_days+1):

        d = inc_date_day(date1,i)


        weekday_num= datetime.datetime.strptime(d, '%Y-%m-%d').weekday()  
        # print('weekday_num=',weekday_num)
        if weekday_num == 5: continue # this is saturday

        print(d)
        ret_dict = get_facebook_youtube_opp(d)
        print('')


    # opp_date = '2023-09-21'
    # ret_dict = get_facebook_youtube_opp(opp_date)







    ##################################################################################
    # following is for debugging
    ##################################################################################

    # num_on_list = 8 # 162
    # d = dfd.iloc[num_on_list].to_dict()
    # social_network = 'youtube'
    # for i in range(5):
    #     video_filepath = 'test_'+social_network+'_'+str(i+1)+'.mp4'
    #     if os.path.exists(video_filepath):continue
    #     generate_blog_video(d,category,social_network,video_filepath,i)
    #     print(video_filepath)

    #     # exit()


    # num_on_list = 4 # 162
    # d = dfd.iloc[num_on_list].to_dict()
    # social_network = 'facebook'
    # for i in range(5,10):
    #     video_filepath = 'test_'+social_network+'_'+str(i+1)+'.mp4'
    #     if os.path.exists(video_filepath):continue
    #     generate_blog_video(d,category,social_network,video_filepath,i)
    #     print(video_filepath)
    #     time.sleep(2)

    
    
    
    # num_on_list = 290 # short
    # d = dfd.iloc[num_on_list].to_dict()
    # pprint.pprint(d)
    # social_network = 'youtube'
    # for i in range(5):
    #     video_filepath = 'test_short_'+social_network+'_'+str(i+1)+'.mp4'
    #     if os.path.exists(video_filepath):continue
    #     generate_blog_video(d,category,social_network,video_filepath,i)
    #     print(video_filepath)
    #     time.sleep(2)


    # num_on_list = 0 # less than 100%
    # d = dfd.iloc[num_on_list].to_dict()
    # social_network = 'facebook'
    # for i in range(5,10):
    #     video_filepath = 'test_less_'+social_network+'_'+str(i+1)+'.mp4'
    #     if os.path.exists(video_filepath):continue
    #     generate_blog_video(d,category,social_network,video_filepath,i)
    #     print(video_filepath)
    #     time.sleep(2)