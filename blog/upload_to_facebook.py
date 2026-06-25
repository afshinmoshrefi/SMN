# this python program uploads videos to facebook page

import json
import requests
import pandas as pd
import os.path
from os import listdir
import glob
import time
import datetime
from datetime import timedelta
import jwt
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.patches import Rectangle
import calendar
import matplotlib
import base64
import textwrap # removes indents in multi line strings
import sys
import random
# from blog_tools import convert_param_base64,json_log,inc_date_day,diff_between_dates,get_keyprovider_token, login_appserver
from blog_tools import json_log,inc_date_day

sys.path.insert(0, '/home/flask')
import config

FACEBOOK_APP_ID = '793368639059153'
FACEBOOK_APP_SECRET = config.FACEBOOK_APP_SECRET
# the long lived token expires on July 4, 2023
FACEBOOK_ACCESS_TOKEN = config.FACEBOOK_ACCESS_TOKEN
# this is the main page of the TradeWave group
FACEBOOK_PAGE_ID = '110182985091209'  # hard to find - instructions on 5/3/2023 at     https://www.facebook.com/help/1503421039731588

#------------------------------------------------------------------------------------------------
# type is 'seasonal' or 'dr' - default is seasonal - dr stands for date range
# check if this facebook post is already posted - if not post and update the tracking files
# config.fb_current_social_media_posts
# config.fb_archive_social_media_post_actions
# 1 facebook posts per day only

def create_facebook_image_post(page_id,message,img_url):

    today_date = datetime.datetime.now().strftime("%Y-%m-%d") 


    
    post_url = f'https://graph.facebook.com/{page_id}/photos'
    payload = {
        'message':message,
        'url':img_url,
        'access_token':FACEBOOK_ACCESS_TOKEN
    }

    r = requests.post(post_url,data=payload)

    if r.status_code != 200: 
        return -1,str(r.status_code)

    dict = json.loads(r.content)
    post_id = dict['id']

    print('post_id=',post_id)

    return post_id 

#------------------------------------------------------------------------------------------------
def delete_facebook_post(pid):

    post_id = pid
    if isinstance(post_id, int):
        post_id = str(post_id)


    post_url = f'https://graph.facebook.com/{post_id}?access_token={FACEBOOK_ACCESS_TOKEN}'
    print('deleting ',post_id)
    r = requests.delete(post_url)
    print(r.status_code)

    




#------------------------------------------------------------------------------------------------
def upload_facebook_video(page_id,description,video_path,thumbnail_path,thumbnail_url,title):


    files = {'source': open(video_path, 'rb')}
    thumb = {'source': open(thumbnail_path, 'rb')}
    
    post_url = f'https://graph.facebook.com/{page_id}/videos'
    payload = {
        'description':description,
        'access_token':FACEBOOK_ACCESS_TOKEN,
        'poster_url':thumbnail_url,
        'title':title,
        'autoplay':'false'
    }

    

    r = requests.post(post_url,files=files,data=payload)

    

    if r.status_code != 200: 
        print(r.text)

        # save the last upload response in logs for debugging and reference
        with open(config.facebook_last_response_output, 'wb') as file:
            file.write(r.content)

        return -1,str(r.status_code)

    dict = json.loads(r.content)
    post_id = dict['id']

    print('video_id=',post_id)

    return post_id 
#------------------------------------------------------------------------------------------------
# open all json files in the tradewave_videos folder and find video_id to delete
#------------------------------------------------------------------------------------------------
def find_remove_video_id(video_id,folder):

    video_folder_list = glob.glob(folder)
    video_folder_list = sorted(video_folder_list,reverse=True)
    found = False
    for f in video_folder_list:
        file_list = glob.glob(f+'*.json')
        json_files = [file for file in file_list if file.endswith(".json")]
        if len(json_files)>0:
            for json_file in json_files:
                with open(json_file, 'r') as j: 
                    d = json.load(j)
                    if 'video_id' in d:
                        json_file_video_id = d['video_id']
                        if video_id == json_file_video_id: # found the video_id in this json file
                            found = True
                if found == True:
                    del d['video_id'] # remove this video_id because we've deleted the video
                    if 'publish_datetime' in d:
                        del d['publish_datetime']
                    with open(json_file, "w") as j:
                        json.dump(d, j, ensure_ascii=False,indent=4) 

                    return f'video_id {video_id} deleted from {json_file}'

    return f'video_id {video_id} not found in any of the json files'
#------------------------------------------------------------------------------------------------
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    
    today_date    = datetime.datetime.now().strftime("%Y-%m-%d")
    tommorow_date = inc_date_day(today_date,1) # creating videos about tomorrow's opportunities
    folder        = f"{config.video_output_folder}{tommorow_date}/" # folder for the videos and json files 

    if len(sys.argv)>1:
        if sys.argv[1] == 'del' or sys.argv[1] == 'delete':
            if len(sys.argv) == 3:  # this is to delete a facebook post
                video_id = sys.argv[2]
                delete_facebook_post (video_id)
                # remove from the json file for this video
                msg=find_remove_video_id(video_id,folder)
                print(msg)


        exit() # this is just to delete so stop here



    
    

    # print(current_time)
    # exit()


    # time to schedule the first and second video to be posted on facebook
    schedule_video_1 = '08:00 AM' # video 1 is just the first one of the list for today
    schedule_video_2 = '12:00 PM'


    scheduled_upload_time = []

    current_time    = datetime.datetime.now()
    
    scheduled_time_1 = datetime.datetime.strptime(schedule_video_1, "%I:%M %p")
    scheduled_time_1 = scheduled_time_1.replace(year=current_time.year, month=current_time.month, day=current_time.day)
    scheduled_upload_time.append( scheduled_time_1)

    scheduled_time_2 = datetime.datetime.strptime(schedule_video_2, "%I:%M %p")
    scheduled_time_2 = scheduled_time_2.replace(year=current_time.year, month=current_time.month, day=current_time.day)
    scheduled_upload_time.append( scheduled_time_2)
    

    ######################################################
    ######################################################
    # tommorow_date = '2023-10-06'  # for debug
    ######################################################
    ######################################################

    weekday_num = datetime.datetime.strptime(tommorow_date, '%Y-%m-%d').weekday()  
    if weekday_num == 5 or weekday_num == 6: 
        exit()  # avoid running when tomorrow is saturday or sunday 


    # check if this folder has been created - 
    # it should have been generated prior to running upload_to_facebook.py by generate_blog_videos.py
    
    folder_exists = os.path.exists(folder)
    if not folder_exists:
        print(f'video folder: {folder} does not exists - run generate_blog_videos.py first')
        exit()

    # get the files in the video folder
    mp4_list = glob.glob(folder+'*.mp4')
    mp4_list = [item for item in mp4_list if 'facebook' in item] # get rid of youtube files



    video_num = 0 # this is used for scheduling the first and second videos at different times of the day

    for video_file in mp4_list:
        print(video_file)

        

        base_name = os.path.basename(video_file)
        json_file = folder + base_name[:-4]+'.json'
        
        with open(json_file, 'r') as f: d = json.load(f)

        # check if this video has already been uploaded - if it has then don't upload again
        if 'video_id' not in d: # this video has been created by generate_blog_videos.py but has not been uploaded 

            # this program runs every hour during the day - it will check the schedule time for video1 and video 2 ..
            # it uploads the video if the current time has passed the schedule time



            if current_time >= scheduled_upload_time[video_num]: # its time to upload this video
            
              
                thumbnail_url  = d['thumbnail_url']
                thumbnail_path = d['thumbnail_path']
                description    = d['description_content'] 
                title          = d['title'] 

                page_id        = FACEBOOK_PAGE_ID
                video_id       = upload_facebook_video(page_id,description,video_file,thumbnail_path,thumbnail_url,title)

                d['video_id']         = video_id
                d['publish_datetime'] = current_time.strftime("%Y-%m-%d %H:%M")

                # test_json = json_file[:-5]+'_test.json'
                with open(json_file, "w") as j:
                    json.dump(d, j, ensure_ascii=False,indent=4) 

                print(f'uploaded video {video_num} to facebook from mp4 file: {video_file}')
            else:
                print(f'video_num:{video_num} is not yet uploaded: {video_file} - scheduled time is: {scheduled_upload_time[video_num]}')
        else:
            print(f'video {video_file} has already been uploaded to facebook')    
            
        video_num += 1 # starts with video_num = 1 and then 2



