

# creates a thumbnail image in any of the social network sizes 
# it creates the thumbnail by opening a background image placing a 
# person image on it and display a banner and 3 rows of info on the screen
# plus a disclaimer on the bottom row
# also saves every thumbnail made to a json file for tracking

# the location of the rectangles are determined by percent of the height and width of the image based on the center axis of 
# the rectangle:
# for example a rectangle height could be 0.15 which means it will be 15% of the height of the image
# to place rectangle of heigth 0.15 and width 0.6 in the center of the image flushed to the right
# the y % would be at 0.5 because y % is based on the center horizantal axis of the rectangle
# the x % would be center vertical axis should be at 1 - (0.6/2) = 0.7  this would make rectangle flushed to the right edge

# this happened unintentionally initially but it worked out about fb and facebook
# fb social_media type is for reports
# facebook social_media type is for videos


import json
import requests
import datetime
import pandas as pd
from PIL import Image, ImageDraw, ImageFont, ImageColor
from blog_tools import get_keyprovider_token,login_appserver ,inc_date_day,format_date,create_title_slug,json_log
import os
import random
import sys
sys.path.insert(0, '/home/flask')
import config



#--------------------------------------------------------------------------------------------------------------------------
thumbnail_size = {
    'tn'                    :(1080,600),   # 1.8 - this is for thumbnails on opp_blog pages
    'fb'                    :(1200,630),   # 1.9
    'facebook'              :(1280,720),   # 1.9  # this is facebook video thumbnails
    'twitter'               :(1600,900),   # 1.8
    'twitter_post'          :(1600,900),   # 1.8
    'twitter_recommended1'  :(1080,1080),  # 1
    'twitter_recommended2'  :(1080,1350),  # 0.8
    'instagram'             :(1080,1080),  # 1
    'linkedin'              :(1200,627),   # 1.9
    'pinterest'             :(1000,1500),  # 0.7
    'youtube'               :(1280,720)    # 1.8
}

#--------------------------------------------------------------------------------------------------------------------------

def get_chart_data(id,opp_date,symbol,daysOut,years,zero_last_year,appserver_token):

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    urlY = config.appserver_url+'/ChartData4/'+str(id)+'/'+opp_date+'/'+symbol+'/'+daysOut+'/'+str(years)+'?token='+appserver_token
    # print('urlY=',urlY)
#     print('0',id,opp_date,symbol,daysOut,years)
    result = requests.get(urlY)
    # check the current year in case we want to zero out the current year.  unless its from seasonal viewer
    current_year = int(today_date[:4])
    api_result = result.json()

    # check the last item of the list 2/24/2022
    if api_result['ChartData4'][-1]['year'] == current_year:
        if zero_last_year == True:
            api_result['ChartData4'][-1]['price']='0,0'
            api_result['ChartData4'][-1]['pct']= '0,0,0'

    return api_result
#--------------------------------------------------------------------------------------------------
def get_company_name(resource_id,symbol):

    symbols_csv = config.available_resources_path[str(resource_id)]
    dfs = pd.read_csv(symbols_csv)[['symbols','name']]
    df=dfs[dfs['symbols'] == symbol]

    company = ''
    if df.shape[0] == 1: company = df['name'].iloc[0]
    return company
#--------------------------------------------------------------------------------------------------

def get_trade_data(resource_id,date,symbol,days,years):

    days_hold_corrected = str(int(days) - 1)

    company = get_company_name(resource_id,symbol)

    keyprovider_token=get_keyprovider_token()
    appserver_token=login_appserver(keyprovider_token)
    cdata=get_chart_data(resource_id,date,symbol,days_hold_corrected,years,False,appserver_token)

    direction = cdata['stats']['Trade Dir']
    date1 = date
    date2 = inc_date_day(date1,int(days)-1) # -1 is to created corrected_date - problem was from my mistake in the past
    avg_gain = cdata['stats']['Avg Profit']

    return company,direction,date1,date2,avg_gain
#--------------------------------------------------------------------------------------------------

#--------------------------------------------------------------------------------------------------------------------------
# this func draws a rectangele and text centered inside of it
def draw_text_rect(image,draw,td):
    
    rect_width = image.size[0] * td['rect_width_pct']
    center_x = td['rect_x_pct'] * image.size[0]
    x1 = center_x - rect_width //2
    x2 = center_x + rect_width //2

    rect_height = image.size[1] * td['rect_height_pct']
    center_y = td['rect_y_pct'] * image.size[1]
    y1 = center_y - rect_height //2
    y2 = center_y + rect_height //2
    
    # calc font_size
    
    text_width = rect_width * td['text_width_factor']
    font,font_size = get_font_size(draw,td['text_content'],text_width,td['font_ttf'])
    # calc text position
    
    # text_width, text_height = draw.textsize(td['text_content'], font=font)
    _,_,text_width, text_height = draw.textbbox((0,0),td['text_content'], font=font)
    
    text_x = (rect_width - text_width)  // 2 + x1
    text_y = td['rect_y_pct'] * image.size[1] - text_height // 2
    
    text_position = (text_x, text_y)
    
    # Calculate the center point of the rectangle
    center_x = (x1 + x2) // 2
    center_y = (y1 + y2) // 2
    
    if td['rect_color'] != 'transparent': # don't draw rectangel if I called the color transparent
        draw.rectangle([(x1, y1), (x2, y2)], fill=td['rect_color'])  # Draw the rectangle

    draw.text(text_position, td['text_content'], font=font, fill=td['font_color']) # Draw the text on the image

    
    
    return image
#--------------------------------------------------------------------------------------------------------------------------
# used to generated a random dark color for banner
def generate_random_dark_color():
    # Generate random values for RGB components in the range 0-127
    red = random.randint(0, 127)
    green = random.randint(0, 127)
    # blue = random.randint(0, 127)
    blue = 127

    return red, green, blue
#--------------------------------------------------------------------------------------------------------------------------
# measure the width of text for each font to get the font creating the width
def get_font_size(draw,text,width,font_ttf):
    
    font_size = 20 # starting font size
    font = ImageFont.truetype(font_ttf, font_size)

    # Loop until the measured text width matches the desired width
    while True:
        w=draw.textbbox((0, 0), text, font=font)[2]
        if w >= width:break
        font_size += 1
        font = ImageFont.truetype(font_ttf, font_size)
    
    return font,font_size
#--------------------------------------------------------------------------------------------------------------------------

def create_thumbnail(tnd):

    
    #########################################################
    ####################### base image ######################
    #########################################################
    image_size = thumbnail_size[tnd['thumbnail_format']]
    image = Image.new('RGBA', image_size, tnd['bg_color']) # Create a blank image with the specified background color and size
    draw = ImageDraw.Draw(image) # Create a draw object
    
    #########################################################
    ################### background image ####################
    #########################################################
    bg_image = Image.open(tnd['bg_image']).convert('RGBA')
    
    aspect_ratio_bg  = bg_image.size[0] / bg_image.size[1]
    aspect_ratio_img = image.size[0]/image.size[1]

    if aspect_ratio_img < aspect_ratio_bg:
        new_height = image.size[1]
        new_width  = int(new_height * aspect_ratio_bg)
    else:
        new_width  = image.size[0]
        new_height = int(new_width/aspect_ratio_img)
        
    bg_image = bg_image.resize((new_width,new_height))
    bg_image.putalpha(tnd['alpha'])
    image.paste(bg_image,(0,0),mask=bg_image)



    
    #####################################################
    ################### person image ####################
    #####################################################
    person_height_factor = tnd['person_height_factor']
    person_shift_left_pct= tnd['person_shift_left_pct']
    am_image = Image.open(tnd['person_image'])
    am_h = int(image.size[1] * person_height_factor)        # new height for person

    # print(image.size[1],am_h)

    am_w = int(am_h * am_image.size[0] / am_image.size[1])  # new width for person
    am_image = am_image.resize((am_w,am_h))

    # am_image.save('test.png')



    shift_left_pixels = int(am_image.size[0] * person_shift_left_pct)
    image.paste(am_image,(-shift_left_pixels,image.size[1]-am_image.size[1]+70),mask=am_image)
    
    #####################################################
    ###################### banner #######################
    #####################################################
    td = {
        'rect_height_pct'  : tnd['banner_height_pct'],
        'rect_width_pct'   : 1,
        'rect_x_pct'       : 0.5,   # location of the center of the banner x
        'rect_y_pct'       : 0.075, # location of the center of the banner y - top:0.075  center:0.5 bottom:0.925
        'rect_color'       : tnd['banner_bg_color'],
        'font_color'       : tnd['banner_font_color'],
        'text_width_factor': tnd['text_width_factor'], # 0.8 means text width is 80% of the rect width
        'text_content'     : tnd['banner_text'],
        'font_ttf'         : tnd['font_ttf'],
        'rotate_angle'     : 0
    }
    image = draw_text_rect(image,draw,td)
    #####################################################
    ####################### logo ########################
    #####################################################
    logo_image = Image.open(tnd['logo_image'])
    # resize the logo to rect_height_pct * text_width_factor for the above td - logo is a square
    logo_h_w = int(td['rect_height_pct']  * image.size[1] * tnd['logo_factor']) 
    rect_h_px= int(image.size[1] * td['rect_height_pct'] )
    logo_y   = rect_h_px - logo_h_w - (rect_h_px-logo_h_w)/2
    logo_y   = int(logo_y)
    logo_x   = (image.size[0] * (1-td['text_width_factor'])/2 - logo_h_w)/2
    logo_x   = int(logo_x)
    if tnd['logo_factor']>0:
        logo_image = logo_image.resize((logo_h_w,logo_h_w))
        image.paste(logo_image,(logo_x,logo_y),mask=logo_image)
    #####################################################
    ###################### row 1 ########################
    #####################################################
    td = {
        'rect_height_pct':tnd['info_rect_height_pct'],
        'rect_width_pct':tnd['info_rect_width_pct'],
        'rect_x_pct':1-(tnd['info_rect_width_pct']/2),
        'rect_y_pct':0.3025, 
        'rect_color':tnd['row1_bg_color'] ,
        'font_color':tnd['row1_font_color'],
        'text_width_factor':tnd['text_width_factor'], # 0.8 means text width is 80% of the rect width
        'text_content':tnd['row1_text'],
        'font_ttf':tnd['font_ttf'],
        'rotate_angle':0
    }
    image = draw_text_rect(image,draw,td)
    #####################################################
    ###################### row 2 ########################
    #####################################################
    td = {
        'rect_height_pct':tnd['info_rect_height_pct'],
        'rect_width_pct':tnd['info_rect_width_pct'],
        'rect_x_pct':1-(tnd['info_rect_width_pct']/2),
        'rect_y_pct':0.525, # top:0.075  center:0.5 bottom:0.925
        'rect_color':tnd['row2_bg_color'] ,
        'font_color':tnd['row2_font_color'],
        'text_width_factor':tnd['text_width_factor'], # 0.8 means text width is 80% of the rect width
        'text_content':tnd['row2_text'],
        'font_ttf':tnd['font_ttf'],
        'rotate_angle':0
    }
    image = draw_text_rect(image,draw,td)
    #####################################################
    ###################### row 3 ########################
    #####################################################
    n = len(tnd['row2_text']) - len (tnd['row3_text'])
    if n < 0:
        n = 0
    row3content = n * ' '+tnd['row3_text']+n*' ' # adding leading and post spaces to make font of content same as row2

    td = {
        'rect_height_pct':tnd['info_rect_height_pct'],
        'rect_width_pct':tnd['info_rect_width_pct'],
        'rect_x_pct':1-(tnd['info_rect_width_pct']/2),
        'rect_y_pct':0.7475, # top:0.075  center:0.5 bottom:0.925
        'rect_color':tnd['row3_bg_color'] ,
        'font_color':tnd['row3_font_color'],
        'text_width_factor':tnd['text_width_factor'], # 0.8 means text width is 80% of the rect width
        'text_content':row3content,
        'font_ttf':tnd['font_ttf'],
        'rotate_angle':0
    }
    image = draw_text_rect(image,draw,td)

 #-------------------------------------------------------------------------------   
    
#############################################################
# water mark on the bottom of the image
#############################################################
    td = {
        'rect_height_pct':0.07,
        'rect_width_pct':0.18,
        'rect_x_pct':0.91, # 0.9 for right and 0.09 for left
        'rect_y_pct':0.855, # top:0.075  center:0.5 bottom:0.925
        'rect_color':'transparent' ,
        'font_color':(200, 200, 200, 128),
        # 'font_color':(128, 128, 128, 128),
        'text_width_factor':tnd['text_width_factor'], # 0.8 means text width is 80% of the rect width
        'text_content':'TradeWave.AI',
        'font_ttf':tnd['font_ttf'],
        'rotate_angle':0
    }

    # draw a watermark TradeWave.AI - comment out to remove the watermark
    image = draw_text_rect(image,draw,td)
#############################################################

#-------------------------------------------------------------------------------

    
    td = {
        'rect_height_pct':tnd['disclaimer_height_pct'],
        'rect_width_pct':1,
        'rect_x_pct':0.5, # 0.9 for right and 0.09 for left
        'rect_y_pct':0.926, # top:0.075  center:0.5 bottom:0.925
        'rect_color':(235,235,235) ,
        'font_color':(128, 128, 128, 128),
        'text_width_factor':0.95, # 0.8 means text width is 80% of the rect width
        'text_content':tnd['disclaimer_text1'],
        'font_ttf':tnd['font_ttf'],
        'rotate_angle':0
    }

    # draw a watermark TradeWave.AI - comment out to remove the watermark
    image = draw_text_rect(image,draw,td)

#-------------------------------------------------------------------------------

    td = {
        'rect_height_pct':tnd['disclaimer_height_pct'],
        'rect_width_pct':1,
        'rect_x_pct':0.5, # 0.9 for right and 0.09 for left
        'rect_y_pct':0.975, # top:0.075  center:0.5 bottom:0.925
        'rect_color':(235,235,235) ,
        'font_color':(128, 128, 128, 128),
        'text_width_factor':0.95, # 0.8 means text width is 80% of the rect width
        'text_content':tnd['disclaimer_text2'],
        'font_ttf':tnd['font_ttf'],
        'rotate_angle':0
    }

    image = draw_text_rect(image,draw,td)


#-------------------------------------------------------------------------------
# write Video on the thumbnail if this is a video image
    td = {
        'rect_height_pct':0.1,
        'rect_width_pct':0.1,
        'rect_x_pct':0.06, # 0.9 for right and 0.09 for left
        'rect_y_pct':0.2, # top:0.075  center:0.5 bottom:0.925
        'rect_color':'transparent',
        'font_color':(178, 178, 178, 178),
        'text_width_factor':0.95, # 0.8 means text width is 80% of the rect width
        'text_content':'VIDEO',
        'font_ttf':tnd['font_ttf'],
        'rotate_angle':0
    }
    if tnd['ttype'] == 'video' and tnd['social_media'] == 'facebook':
        image = draw_text_rect(image,draw,td)



    image=image.convert('RGB')

    return image
#--------------------------------------------------------------------------------------------------

#--------------------------------------------------------------------------------------------------

def pick_random_image(folder_path):
    image_files = []
    for filename in os.listdir(folder_path):
        if filename.endswith(".jpg") or filename.endswith(".png"):
            image_files.append(filename)
    
    if not image_files:
        print("No image files found in the folder.")
        return None
    
    random_image = random.choice(image_files)
    random_image_path = os.path.join(folder_path, random_image)
    return random_image_path


#--------------------------------------------------------------------------------------------------


#--------------------------------------------------------------------------------------------------
# this function can be used to create a social media thumbnail - it then stores it in a accessible
# folder so it is accessible by web - it then returns the server path and web path to the image
# sm values can be set to fb twitter instagram linkedin pinterest youtube
# result image will be saved in config.socialmedia_thumbnail_folder

# by default if no category specified, it shows afshin /am images - other categories like seasonal report, its random people
def create_socialmedia_thumbnail(sm,resource_id,date,symbol,days,dir,avg_gain,years,title_pre,category = config.category_date_range_report,ttype='report'): # ttype could be report or video - changes background image
    
    date1 = date
    date2 = inc_date_day(date1,int(days)-1) # -1 is to created corrected_date - problem was from my mistake in the past
    company = get_company_name(resource_id,symbol)
    
    if sm not in thumbnail_size:
        return 'error: '+sm+' not in the list of supported socialmedias'
    

    if ttype == 'video' and sm == 'facebook':
        bg_image = '/home/flask/blog/images/background_video/background_for_videos1.png'
    else:
        bg_image = pick_random_image('/home/flask/blog/images/background')


    print (bg_image)

    #---------------------------------------------------
    # afshin person images are placed here
    #---------------------------------------------------

    # if category == config.category_date_range_report:
    #     person_image = pick_random_image('/home/flask/blog/images/person/am') # all date-range-report use afshin images
    # else:
    #     person_image = pick_random_image('/home/flask/blog/images/person')    # top 10 seasonal reports use non afshin images


    # replaced afshin images to the same images picked for all - to put afshin images back uncomment above
    person_image = pick_random_image('/home/flask/blog/images/person')    # top 10 seasonal reports use non afshin images
    


    # get the data needed to create the thumbnail
    # 6/14/2023 its redundant - I was making double calls that failed with wordpress apigate - unnecessary
    # company,dir,date1,date2,avg_gain = get_trade_data(resource_id,date,symbol,days,years)

    # print(company,dir,date1,date2,avg_gain)

    xdate1=format_date(date1)
    xdate2=format_date(date2)

    
    banner_text = f'{title_pre}{dir.capitalize()} Wave Trade in {company}'

    if dir.lower() == 'long':
        row1_text   = f'Buy {symbol} On {xdate1}'
        row2_text   = f'Sell {symbol} By {xdate2}'
        row1_colors = [(152, 251, 152),(0,100,0)]
        row2_colors = [(255,218,185),(165,56,55)]
    else:
        row1_text   = f'Sell {symbol} On {xdate1}'
        row2_text   = f'Buy {symbol} By {xdate2}'
        row2_colors = [(152, 251, 152),(0,100,0)]
        row1_colors = [(255,218,185),(165,56,55)]
    row3_text   = f'Average Gain: {avg_gain}'


    # banner_text = 'News: Long Seasonal Trade in DexCom'
    # row1_text = 'Buy DXCM On May 29th'
    # row2_text = 'Sell DXCM By Aug 13th'
    # row3_text = 'AVG Gain: 29.5%'

    # default values
    info_rect_width_pct = 0.6
    text_width_factor = 0.8
    logo_factor = 0.7
    person_shift_left_pct = 0

    # exceptions
    if sm == 'instagram':
        info_rect_width_pct = 0.5
        text_width_factor = 0.75
        logo_factor = 0.6
    if sm == 'fb':
        person_shift_left_pct = 0

    alpha = 125

    if ttype == 'video' and sm == 'facebook':
        alpha = 255

    tnd={
        'bg_color'             : (0,0,0),
        'thumbnail_format'     : sm,
        'font_ttf'             : '/home/flask/blog/images/font/Roboto-Bold.ttf',  
        'bg_image'             : bg_image,
        'alpha'                : alpha,
        'person_image'         : person_image,
        'logo_image'           : config.logo_thumbnail, # this logo is place on the top left of the thumbnails
        'logo_factor'          : logo_factor,  # resize factor for the logo
        'person_height_factor' : 0.9,  # 0.5 means person is 1/2 height of base
        'person_shift_left_pct': person_shift_left_pct,  # 0.1 means person shifted left 10%
        'banner_height_pct'    : 0.15, # 0.15 would mean height is 15% of image height
        'info_rect_height_pct' : 0.14,
        'info_rect_width_pct'  : info_rect_width_pct,
        'text_width_factor'    : text_width_factor,  # means text will take 80% of the width of the rectangle
        'banner_text'          : banner_text,
        'disclaimer_height_pct': 0.05,
        'disclaimer_bg_color'  : (230,230,230),
        'disclaimer_text'      : f'Based on {years}-year history. Past performance does not ensure future results.  TradeWave.AI',
        
        'disclaimer_text1'     : f'Based on {years}-year history. This is not investment advice. Past performance does  ',
        'disclaimer_text2'     : f'not ensure future results.  Consult a financial advisor before taking any trades.',

        # 'banner_bg_color'      : (0,0,128),
        'banner_bg_color'      : generate_random_dark_color(),
        'banner_font_color'    : 'white',
        'row1_text'            : row1_text,
        'row1_bg_color'        : row1_colors[0],
        'row1_font_color'      : row1_colors[1],
        'row2_text'            : row2_text,
        'row2_bg_color'        : row2_colors[0],
        'row2_font_color'      : row2_colors[1],
        'row3_text'            : row3_text,
        'row3_bg_color'        : 'lightblue',
        'row3_font_color'      : 'blue',
        'ttype'                : ttype, # this is either report or video
        'social_media'         : sm # used to conditionally put the word video only on the facebook video thumbnails
    }

    image = create_thumbnail(tnd)







    ####################################################################################################



    # get title and slug for this post
    title,slug=create_title_slug(company, symbol, date1, date2, years,category)
    sm_tn_folder = config.socialmedia_thumbnail_folder


    # filename = f'{sm_tn_folder}{sm}/{slug}.png'
    # url      = f'{config.img_folder}thumbnails/{sm}/{slug}.png'
    base_year= date[:4]
    filename = f'{sm_tn_folder}{sm}/{base_year}/{date}/{slug}.jpg'
    url      = f'{config.img_folder}thumbnails/{sm}/{base_year}/{date}/{slug}.jpg'


    path = os.path.dirname(filename)
    os.makedirs(path, exist_ok=True)
    image.save(filename)

    # save the thumbnail to a json file
    thumbnail_info={
        'sm': sm,
        'filename': filename,
        'url': url,
        'resource_id': resource_id,
        'date1': date,
        'symbol': symbol,
        'days': days,
        'years': years,
        'category': category, # category of the report - initially they are all seasonal reports
        'creation_datetime':datetime.datetime.now().isoformat()
    }

    # save a record of each thumbnail generated
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    cur_year   = today_date[:4]

    json_filename = f'{config.thumbnails_json_file}{cur_year}/tn_tracking-{today_date}.json'

    # print('\n\n\n','jjjjjjjjjjjjjjjjjjjjjson_filename=',json_filename,'\n\n\n')

    folder_path = os.path.dirname(json_filename) # create the folders if needed
    os.makedirs(folder_path, exist_ok=True) 
    json_log(json_filename,'add',thumbnail_info)

    return filename,url

#--------------------------------------------------------------------------------------------------        




if __name__ == '__main__':

    # thumbnail_path,thumbnail_url = create_socialmedia_thumbnail(social_type,resourceID,opp_dict['Date'],opp_dict['Symbol'],opp_dict['DaysOut'],opp_dict['Direction'],opp_dict['Avg Profit']+'%',years,title_pre,category)

    path,url=create_socialmedia_thumbnail('youtube',0,'2023-08-12','UNH','259','Long','12.34%','14','',config.category_date_range_report,'video')
    print(url)

    # company,direction,date1,date2,avg_gain = get_trade_data('0','2023-05-12','UNH','66','10')
    # print(company,direction,date1,date2,avg_gain)


    # for i in range(25,26):

    #     path,url=create_socialmedia_thumbnail('fb',0,'2023-05-05','MSFT',str(i),'10','Profit Alert: ',config.category_date_range_report)

    #     print (path)
    #     print (url)





    # x=get_company_name(7,'HE')
    # print(x)

    # tnd={
    #     'bg_color'             : (0,0,0),
    #     'thumbnail_format'     : 'youtube',
    #     'font_ttf'             : '/home/flask/blog/images/font/Roboto-Bold.ttf', #arial and arialbd  
    #     'bg_image'             : 'images/background/istockphoto-506474410-1024x1024.jpg',
    #     'alpha'                : 125,
    #     'person_image'         : 'images/person/afshin_no_background.png',
    #     'logo_image'           : 'images/logo/logo_am2.png',
    #     'logo_factor'          : 0.7,  # resize factor for the logo
    #     'person_height_factor' : 0.6,  # 0.5 means person is 1/2 height of base
    #     'person_shift_left_pct': 0.1,  # 0.1 means person shifted left 10%
    #     'banner_height_pct'    : 0.15, # 0.15 would mean height is 15% of image height
    #     'info_rect_height_pct' : 0.15,
    #     'info_rect_width_pct'  : 0.6,
    #     'text_width_factor'    : 0.8,  # means text will take 80% of the width of the rectangle
    #     'banner_text'          : 'News: Long Seasonal Trade in DexCom',
    #     'banner_bg_color'      : (0,0,128),
    #     'banner_font_color'    : 'white',
    #     'row1_text'            : 'Buy DXCM on May 29th',
    #     'row1_bg_color'        : (152, 251, 152),
    #     'row1_font_color'      : (0,100,0),
    #     'row2_text'            : 'Sell DXCM By Aug 13th',
    #     'row2_bg_color'        : (255,218,185),
    #     'row2_font_color'      : (165,56,55),
    #     'row3_text'            : 'AVG Gain: 29.5%',
    #     'row3_bg_color'        : 'lightblue',
    #     'row3_font_color'      : 'blue'
    # }


    # image = create_thumbnail(tnd)
    # image.save('test.png')





