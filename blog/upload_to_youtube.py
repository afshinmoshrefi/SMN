#!/usr/bin/python

# this is an example from youtube data api that can upload a video to youtube

# command line for running this
# python .\google_youtube.py --noauth_local_webserver --file=./tradewave_videos/2023-10-05/youtube_NUE_video_l1_2023-10-05.mp4 --title="My first example title" --description="example youtube description" --keywords="surfing,trading" --category="27" --privacyStatus="public"

# import httplib
import httplib2
import os
import random
import sys
import time
import datetime
import argparse
from blog_tools import json_log,inc_date_day
import glob
import json

#  had to change apiclient to googleapiclient otherwise was not found

from googleapiclient.discovery import build 
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import argparser, run_flow 


sys.path.insert(0, '/home/flask')
import config

# Explicitly tell the underlying HTTP transport library not to retry, since
# we are handling retry logic ourselves.
httplib2.RETRIES = 1

# Maximum number of times to retry before giving up.
MAX_RETRIES = 10

# Always retry when these exceptions are raised.
# RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, httplib2.NotConnected,
#   httplib2.IncompleteRead, httplib2.ImproperConnectionState,
#   httplib2.CannotSendRequest, httplib2.CannotSendHeader,
#   httplib2.ResponseNotReady, httplib2.BadStatusLine)

# Always retry when an apiclient.errors.HttpError with one of these status
# codes is raised.
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

# The CLIENT_SECRETS_FILE variable specifies the name of a file that contains
# the OAuth 2.0 information for this application, including its client_id and
# client_secret. You can acquire an OAuth 2.0 client ID and client secret from
# the Google API Console at
# https://console.cloud.google.com/.
# Please ensure that you have enabled the YouTube Data API for your project.
# For more information about using OAuth2 to access the YouTube Data API, see:
#   https://developers.google.com/youtube/v3/guides/authentication
# For more information about the client_secrets.json file format, see:
#   https://developers.google.com/api-client-library/python/guide/aaa_client_secrets
CLIENT_SECRETS_FILE = "youtube_client_secrets.json"

# This OAuth 2.0 access scope allows an application to upload files to the
# authenticated user's YouTube channel, but doesn't allow other types of access.
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

# This variable defines a message to display if the CLIENT_SECRETS_FILE is
# missing.
MISSING_CLIENT_SECRETS_MESSAGE = """
WARNING: Please configure OAuth 2.0

To make this sample run you will need to populate the client_secrets.json file
found at:

   %s

with information from the API Console
https://console.cloud.google.com/

For more information about the client_secrets.json file format, please visit:
https://developers.google.com/api-client-library/python/guide/aaa_client_secrets
""" % os.path.abspath(os.path.join(os.path.dirname(__file__),
                                   CLIENT_SECRETS_FILE))

VALID_PRIVACY_STATUSES = ("public", "private", "unlisted")

#------------------------------------------------------------------------
def get_authenticated_service(args):

  flow = flow_from_clientsecrets(CLIENT_SECRETS_FILE,scope=YOUTUBE_UPLOAD_SCOPE, message=MISSING_CLIENT_SECRETS_MESSAGE)

  print('storage=',"%s-oauth2.json" % sys.argv[0])

  storage = Storage("%s-oauth2.json" % sys.argv[0])
  credentials = storage.get()

  if credentials is None or credentials.invalid:
    credentials = run_flow(flow, storage, args)

  return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION,
    http=credentials.authorize(httplib2.Http()))
#------------------------------------------------------------------------

def initialize_upload(youtube, options):
  tags = None
  if options.keywords:
    tags = options.keywords.split(",")

  body=dict(
    snippet=dict(
      title=options.title,
      description=options.description,
      tags=tags,
      categoryId=options.category
    ),
    status=dict(
      privacyStatus=options.privacyStatus
    )
  )

  # Call the API's videos.insert method to create and upload the video.
  insert_request = youtube.videos().insert(
    part=",".join(body.keys()),
    body=body,
    # The chunksize parameter specifies the size of each chunk of data, in
    # bytes, that will be uploaded at a time. Set a higher value for
    # reliable connections as fewer chunks lead to faster uploads. Set a lower
    # value for better recovery on less reliable connections.
    #
    # Setting "chunksize" equal to -1 in the code below means that the entire
    # file will be uploaded in a single HTTP request. (If the upload fails,
    # it will still be retried where it left off.) This is usually a best
    # practice, but if you're using Python older than 2.6 or if you're
    # running on App Engine, you should set the chunksize to something like
    # 1024 * 1024 (1 megabyte).
    media_body=MediaFileUpload(options.file, chunksize=-1, resumable=True)
  )

  video_id=resumable_upload(insert_request)
  return video_id
#------------------------------------------------------------------------
# def insertThumbnail(youtube,videoID,thumbnailPath):
#   youtube.thumbnails().set(videoId=videoId,media_body=MediaFileUpload(thumbnailPath))
#------------------------------------------------------------------------
# This method implements an exponential backoff strategy to resume a
# failed upload.
def resumable_upload(insert_request):
  response = None
  error = None
  retry = 0
  video_id = None

  while response is None:
    try:
      print ("Uploading file...")
      status, response = insert_request.next_chunk()
      if response is not None:
        if 'id' in response:
          print ("Video id '%s' was successfully uploaded." % response['id'])
          video_id = response['id']
          # # upload thumbnail
          # thumbnail_path = "/var/www/html/wp-content/uploads/p/thumbnails/facebook/2023/2023-10-06/25-year-tradewave-report-nucor-nue-2023-10-06-to-2023-12-25.jpg",
          # youtube.thumbnails().set(videoId=response['id'],media_body=MediaFileUpload(thumbnail_path))



        else:
          exit("The upload failed with an unexpected response: %s" % response)
    except HttpError as e:
      if e.resp.status in RETRIABLE_STATUS_CODES:
        error = "A retriable HTTP error %d occurred:\n%s" % (e.resp.status,
                                                             e.content)
      else:
        raise
    except RETRIABLE_EXCEPTIONS as e:
      error = "A retriable error occurred: %s" % e

    if error is not None:
      print (error)
      retry += 1
      if retry > MAX_RETRIES:
        exit("No longer attempting to retry.")

      max_sleep = 2 ** retry
      sleep_seconds = random.random() * max_sleep
      print ("Sleeping %f seconds and then retrying..." % sleep_seconds)
      time.sleep(sleep_seconds)

  return video_id
#-----------------------------------------------------------------------------------------------------------------------------------
def upload_youtube_video(description,video_file,thumbnail_path,thumbnail_url,title):

  
  args = argparse.Namespace(
    auth_host_name='localhost',
    auth_host_port=[8080, 8090],
    category='27',
    description=description,
    file=video_file,
    keywords='surfing,trading',
    logging_level='ERROR',
    noauth_local_webserver=True,
    privacyStatus='private',
    title=title
  )

  print('thumbnail_path=',thumbnail_path)

  youtube = get_authenticated_service(args)

  try:
    video_id=initialize_upload(youtube, args)
    print('video_id=',video_id)
    # upload thumbnail
    request = youtube.thumbnails().set( videoId=video_id,  media_body=MediaFileUpload(thumbnail_path) )
    response = request.execute()
    print(response)
  except HttpError as e:
    print ("An HTTP error %d occurred:\n%s" % (e.resp.status, e.content))


  return video_id

#-----------------------------------------------------------------------------------------------------------------------------------
def delete_youtube_post (video_id):
  
  args = argparse.Namespace(
    auth_host_name='localhost',
    auth_host_port=[8080, 8090],
    category='27',
    description='',
    file='video_file',
    keywords='surfing,trading',
    logging_level='ERROR',
    noauth_local_webserver=True,
    privacyStatus='private',
    title=''
  )

  youtube = get_authenticated_service(args)
  request = youtube.videos().delete(id=video_id)
  response = request.execute()

  

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
#########################################################################################################################################
#                                                                 Main
#########################################################################################################################################
if __name__ == '__main__':
  
  
  
  today_date    = datetime.datetime.now().strftime("%Y-%m-%d")
  tommorow_date = inc_date_day(today_date,1) # creating videos about tomorrow's opportunities
  ######################################################
  ######################################################
  tommorow_date = '2023-10-06'  # for debug
  ######################################################
  ######################################################
  folder        = f"{config.video_output_folder}{tommorow_date}/" # folder for the videos and json files 

  if len(sys.argv)>1:
      if sys.argv[1] == 'del' or sys.argv[1] == 'delete':
          if len(sys.argv) == 3:  # this is to delete a facebook post
              video_id = sys.argv[2]
              delete_youtube_post (video_id)
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
  mp4_list = [item for item in mp4_list if 'youtube' in item] # get rid of youtube files



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
              
              print(description)

              video_id       = upload_youtube_video(description,video_file,thumbnail_path,thumbnail_url,title)

              d['video_id']         = video_id
              d['publish_datetime'] = current_time.strftime("%Y-%m-%d %H:%M")

              with open(json_file, "w") as j:
                  json.dump(d, j, ensure_ascii=False,indent=4) 

              print(f'uploaded video {video_num} to facebook from mp4 file: {video_file}')
          else:
              print(f'video_num:{video_num} is not yet uploaded: {video_file} - scheduled time is: {scheduled_upload_time[video_num]}')
      else:
        print(f'video {video_file} has already been uploaded to youtube')

      video_num += 1 # starts with video_num = 1 and then 2


      


