# this is to look at and remove generated posts 2/6/2023
# when done a single parameter will control the deletion of posts - num_days_keep_posts
# when it is set to 0 and the app runs, it remove everything

import datetime
from datetime import timedelta
import requests
import glob
import time
import base64
import json
import sys
import os
from slugify import slugify
sys.path.insert(0, '/home/flask')
import config

# if num_days_old > -1 then we remove older blogs in category - if no category, we remove it from all categories
# if max_total has exceeded for the catgory or all if no category.  delete the oldest until total is less than max_total
def get_all_post_titles_and_ids(header,category,num_days_old,max_total):
    page = 1
    all_posts = []

    # if category is not 0, retrieve only posts with that category number
    if category == 0:
        url = config.post_endpoint_url + "?per_page=100&_fields=title,id,slug,categories&page={}"
    else :  
        url = config.post_endpoint_url + f"?categories={category}&per_page=100&_fields=title,id,slug,categories&page="+"{}"

    url = config.post_endpoint_url + "?per_page=100&page={}"

    while True:
        response = requests.get(url.format(page), headers=header)
        if response.status_code == 200:
            posts = response.json()
            for post in posts:

                rdate = '' # extract from post title
                title = post["title"]["rendered"]
                cat   = post["categories"][0]

                if   cat == config.category_report:
                    rdate = title[-10:]
                elif cat == config.category_top10:
                    rdate = title[-10:]
                elif cat == config.category_opp_top10:
                    rdate = title[-10:]
                elif cat == config.category_top10_archive:
                    rdate = ''              
                elif cat == config.category_date_range_report:
                    rdate = title[-24:-14]

                # don't add archive category - that does not get removed
                if post["categories"][0] != config.category_top10_archive:
                    all_posts.append({
                        "title"   : title,
                        "id"      : post["id"],
                        "category": post["categories"][0],
                        "slug"    : post["slug"],
                        "date"    : rdate,
                    })
            
            if len(posts) < 100:
                break
            page += 1
        else:
            break
    return all_posts
#----------------------------------------------------------------------------------------------------------------------------------------
def remove_posts(num_days_old,category,userid,slug,post_id,max_total):


    credentials = config.username + ':' + config.password
    token = base64.b64encode(credentials.encode())
    header = {'Authorization': 'Basic ' + token.decode('utf-8')}
    
    all_posts = get_all_post_titles_and_ids(header,category,num_days_old,max_total)



    print(len(all_posts),' posts returned for delete in wordpress')

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    delete_reports_older_than_date = (datetime.datetime.strptime(today_date, '%Y-%m-%d') + timedelta(days=-num_days_old)).strftime('%Y-%m-%d')

    print('delete_reports_older_than_date=',delete_reports_older_than_date)

    # set which categories of posts to remove

    all_posts = sorted(all_posts, key=lambda x: x['date'],reverse=True) # sort the list of dictionaries - newest on top

    for post in all_posts:

        title     = post['title']
        post_slug = slugify(title)
        id        = post['id']
        category  = post['category']
        # date1     = title[-24:-14] # this is the date1 of the post 
        date1     = post['date'] # this is derived based on category
        base_year = date1[:4]

        # these 2 categories of posts, have 3 images each that need to be removed 
        if category == config.category_report or category == config.category_date_range_report:
            # derive the path to the 3 images to be deleted
            subfolders = f'{base_year}/{date1}/'
            b_img = 'gain-loss-barchart-'+post_slug+'.png'
            c_img = 'cumulative-return-'+post_slug+'.png'
            s_img = 'trend-chart-'+post_slug+'.png'
            b_img = config.chart_root_folder+subfolders+b_img
            c_img = config.chart_root_folder+subfolders+c_img
            s_img = config.chart_root_folder+subfolders+s_img

            # this removes the 3 images from the server

            if os.path.exists(b_img):os.remove(b_img)
            if os.path.exists(c_img):os.remove(c_img)
            if os.path.exists(s_img):os.remove(s_img)

            # remove the subfolder if its empty
            subfolder_for_img = config.chart_root_folder+subfolders

            if os.path.exists(subfolder_for_img):
                x = os.listdir(subfolder_for_img)
                
                if not x: # folder is empty
                    print('#################################')
                    print('removing ',subfolder_for_img)
                    print('#################################')
                    os.rmdir(subfolder_for_img)

        #--------------------------------------------------------------------------
        # this removes the post from wordpress - posts with images remove images above
        response2= requests.delete(config.post_endpoint_url+str(id), headers=header)
        if response2.status_code == 200:
            print(title+" deleted")

        else:
            print(response2['title']['rendered']+"Request failed with status code:", response2.status_code)
        

###################################################################################################################################
###################################################################################################################################
if __name__ == '__main__':

    # category_report           
    # category_top10            
    # category_opp_top10        
    # category_top10_archive    
    # category_date_range_report

    num_days_old = -100
    category     = 0
    userid       = 0
    slug         = ''
    post_id      = 0
    max_total    = 10



    remove_posts(num_days_old,category,userid,slug,post_id,max_total)

    



