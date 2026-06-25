# apigate setting in ump need to turn on api : Activate/Hold "Get all User Data" API Call
import pandas as pd
import sys
import requests
import datetime
from get_top10_data import load_top10,get_chart_data
from blog_tools import get_keys
import base64
sys.path.insert(0, '/home/flask')
import config

credentials = config.username + ':' + config.password
post_token = base64.b64encode(credentials.encode())
header = {'Authorization': 'Basic ' + post_token.decode('utf-8')}
redirect_endpoint_url = config.domain_root + 'wp-json/redirection/v1/'

#####################################################################################################################
def create_new_redirect(url1,url2,redirect_endpoint_url,header):
    if url1[-1] == '/':url1=url1[:-1]
        
    payload = {
        'source': url1,
        'url'   : url1+'/',
        'action_data': {'url': url2},
        'regex': False,
        'group_id': 1,
        'action_type': 'url',
        'match_type': 'url'
    }
    
    response = requests.post(config.redirect_endpoint_url+'redirect',json=payload ,headers=header)
    print('Create Redirect:',response.status_code,response.reason)
    redir = response.json() 
#####################################################################################################################
def get_redirect(url,redirect_endpoint_url,header): # return json for the source url 
     # this is the return id if there is a match or is -1
    ret_json = {}
    response = requests.get(redirect_endpoint_url+'redirect', headers=header)
    if response.status_code > 201:
        print('Get Redirects:',response.status_code,response.reason)
    else:
        redir = response.json()
        lst=redir['items']

        for r in lst:

            id=r['id']
            j_url=r['url']
            match_url=r['match_url']
            action_url=r['action_data']['url']
            if url == match_url or url == j_url:
                ret_json = r
                break
    print('Get Redirects:',response.status_code,response.reason)         
    return ret_json  
#####################################################################################################################
def update_redirect(ret_json,dest_url,redirect_endpoint_url,header):

    # update the json with the new dest url
    ret_json['action_data']['url']= dest_url
    url = redirect_endpoint_url+'redirect/'+str(ret_json['id'])
    # print(url)
    # exit()
    response = requests.post(url,json=ret_json, headers=header)
    print('Update Redirect:',response.status_code,response.reason)

#####################################################################################################################
def redirect_url (src_url, dest_url):

    rj = get_redirect('/top10',redirect_endpoint_url,header) # checks againts the url field

    if not bool(rj) : # dictionary is empty
        print('creating a new redirect')
        if dest_url[1:] != '/':dest_url='/'+dest_url
        create_new_redirect(src_url,dest_url)
    else:
        print('updating existing redirect')
        print(rj)
        update_redirect(rj,dest_url,redirect_endpoint_url,header)

#####################################################################################################################
if __name__ == '__main__':
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    

    redirect_url ('/top10', '/learn')
