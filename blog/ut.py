# -*- coding: utf-8 -*-

# Sample Python code for youtube.thumbnails.set
# NOTES:
# 1. This sample code uploads a file and can't be executed via this interface.
#    To test this code, you must run it locally using your own API credentials.
#    See: https://developers.google.com/explorer-help/code-samples#python
# 2. This example makes a simple upload request. We recommend that you consider
#    using resumable uploads instead, particularly if you are transferring large
#    files or there's a high likelihood of a network interruption or other
#    transmission failure. To learn more about resumable uploads, see:
#    https://developers.google.com/api-client-library/python/guide/media_upload


# from googleapiclient.discovery import build 
# from googleapiclient.errors import HttpError
# from googleapiclient.http import MediaFileUpload
# from oauth2client.client import flow_from_clientsecrets
# from oauth2client.file import Storage
# from oauth2client.tools import argparser, run_flow 

import os

import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors

from googleapiclient.http import MediaFileUpload

scopes = ["https://www.googleapis.com/auth/youtube.force-ssl"]

CLIENT_SECRETS_FILE = "youtube_client_secrets.json"
VIDEO_ID = "V4ZgQlXk9q0"
THUMBNAIL_FILE = "/var/www/html/wp-content/uploads/p/thumbnails/facebook/2023/2023-10-06/25-year-tradewave-report-nucor-nue-2023-10-06-to-2023-12-25.jpg"

def main():
    # Disable OAuthlib's HTTPS verification when running locally.
    # *DO NOT* leave this option enabled in production.
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    api_service_name = "youtube"
    api_version = "v3"

    # Get credentials and create an API client
    flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes)
    credentials = flow.run_console()
    exit()
    youtube = googleapiclient.discovery.build(api_service_name, api_version, credentials=credentials)

    request = youtube.thumbnails().set( videoId=VIDEO_ID,  media_body=MediaFileUpload(THUMBNAIL_FILE) )
    response = request.execute()

    print(response)

if __name__ == "__main__":
    main()