# changed the parameters of 
# run_opp_blog_generation(opp_date):
# added parameter html_table_only boolean
# this is used to only create an html table for each opportunities list

import json
import requests
import pandas as pd
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
import matplotlib
import base64
import textwrap  # removes indents in multi line strings
import sys
import random
from blog_tools import convert_param_base64, json_log, inc_date_day as _inc_date_day, diff_between_dates, get_keyprovider_token, login_appserver

from fb_post_template import get_random_facebook_content
# from thumbnails import create_socialmedia_thumbnail
from thumbnail_renderer import create_socialmedia_thumbnail

from get_top10_data import load_top10, get_chart_data
from slugify import slugify
sys.path.insert(0, '/home/flask')
import config

FACEBOOK_APP_ID = config.FACEBOOK_APP_ID
FACEBOOK_APP_SECRET = config.FACEBOOK_APP_SECRET
FACEBOOK_ACCESS_TOKEN = config.FACEBOOK_ACCESS_TOKEN
FACEBOOK_PAGE_ID = config.FACEBOOK_PAGE_ID
FACEBOOK_OPP_PAGES = config.FACEBOOK_OPP_PAGES

SESSION = requests.Session()


# ------------------------------------------------------------------------------------------------
def inc_date_day(d, i):
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')
# ------------------------------------------------------------------------------------------------


# if multiple pages are used, return the correct token (kept simple per current setup)
def get_page_token(page_id):
    return FACEBOOK_ACCESS_TOKEN


# ------------------------------------------------------------------------------------------------
# type is 'seasonal' or 'dr' - default is 'seasonal'
# keep 1 facebook post per day for 'seasonal'
# create -> upload photo, then fetch feed story post_id and store it
def create_facebook_post(page_id, message, img_url, type='seasonal'):
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    post_id = ''
    photo_id = ''
    permalink = ''
    file_exists = os.path.exists(config.fb_current_social_media_posts)
    post_exists = False

    if file_exists:
        with open(config.fb_current_social_media_posts, 'r') as f:
            post_list = json.load(f)
            for p in post_list:
                # only 1 automatic 'seasonal' post per day
                if type == 'seasonal' and p.get('datetime', '')[:10] == today_date:
                    post_exists = True
                    post_id = p.get('post_id')  # feed story id if stored by new code
                    photo_id = p.get('photo_id')  # old code may not have this
                    break
                elif type == 'dr':
                    # allow multiples but if exact same img_url already posted, treat as exists
                    if img_url == p.get('img_url'):
                        post_exists = True
                        post_id = p.get('post_id')
                        photo_id = p.get('photo_id')
                        break

    if post_exists:
        return post_id or photo_id or '', 'exist'

    # create a new photo post
    post_url = f'https://graph.facebook.com/{page_id}/photos'
    payload = {
        'message': message,
        'url': img_url,
        'access_token': get_page_token(page_id)
    }

    r = SESSION.post(post_url, data=payload)
    if r.status_code != 200:
        try:
            body = r.json()
        except Exception:
            body = r.text
        print('facebook photo create failed', r.status_code, body)
        return -1, str(r.status_code)

    resp = r.json()
    photo_id = resp.get('id', '')

    # fetch the feed story id for the uploaded photo
    # important fix: use post_id for future deletes
    info = SESSION.get(
        f'https://graph.facebook.com/{photo_id}',
        params={
            'fields': 'post_id,permalink_url',
            'access_token': get_page_token(page_id)
        }
    )
    feed_post_id = ''
    permalink = ''
    if info.status_code == 200:
        j = info.json()
        feed_post_id = j.get('post_id', '')
        permalink = j.get('permalink_url', '')
    else:
        print('warning: could not fetch feed post_id for photo', photo_id, info.status_code, info.text)

    post_id = feed_post_id or photo_id  # prefer feed post id, fallback to photo id

    post_info = {
        'sm': 'fb',
        'datetime': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'page_id': page_id,
        'post_id': post_id,          # feed story id
        'photo_id': photo_id,        # keep for debug
        'permalink': permalink,      # handy for manual checks
        'message': message,
        'img_url': img_url,
        'type': type
    }

    # save to tracking file(s)
    if not file_exists:
        with open(config.fb_current_social_media_posts, 'w') as f:
            json.dump([post_info], f, indent=4)
    else:
        with open(config.fb_current_social_media_posts, 'r') as f:
            post_list = json.load(f)
        post_list.append(post_info)
        with open(config.fb_current_social_media_posts, 'w') as f:
            json.dump(post_list, f, indent=4)

    return post_id, 'created'
# ------------------------------------------------------------------------------------------------


def _remove_post_from_log(post_id):
    file_exists = os.path.exists(config.fb_current_social_media_posts)
    if not file_exists:
        return
    with open(config.fb_current_social_media_posts, 'r') as f:
        post_list = json.load(f)
    if not post_list:
        return
    updated_post_list = [d for d in post_list if str(d.get("post_id")) != str(post_id)]
    with open(config.fb_current_social_media_posts, 'w') as f:
        json.dump(updated_post_list, f, indent=4)


def delete_facebook_post(pid):
    # delete the feed story id if possible
    post_id = str(pid)
    url = f'https://graph.facebook.com/{post_id}'
    params = {'access_token': FACEBOOK_ACCESS_TOKEN}
    print('deleting', post_id)
    r = SESSION.delete(url, params=params)
    print('DELETE status', r.status_code, 'body:', getattr(r, 'text', ''))
    # even if delete fails, attempt to remove from local log to avoid stale state looping
    _remove_post_from_log(post_id)


# ------------------------------------------------------------------------------------------------
def top10_link(id):  # id is for financial group
    num = 10
    fg = config.available_resources
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    post_title = f'Top {num} Seasonal Patterns {fg[str(id)]}  {today_date}-t'  # -t will jump to thumbnails 5/18/2023
    post_slug = slugify(post_title)
    return config.domain_root + post_slug
# ------------------------------------------------------------------------------------------------


# post one of the top10 entries based on fg and row
def post_one_of_top10(df, fg, row, hashtags):
    idx = df['post_title'].iloc[row].find("Year")
    years = df['post_title'].iloc[row][:idx - 1]
    symbol = df['Symbol'].iloc[row]
    date1 = df['Date'].iloc[row]
    date2 = df['Date2'].iloc[row]
    slug = df['opp_slug'].iloc[row]
    daysout = df['DaysOut'].iloc[row]
    company = df['company'].iloc[row]
    dir = df['Direction'].iloc[row]
    avg_p = df['Avg Profit'].iloc[row]
    title_pre = 'WAVE ALERT: '
    category = config.category_date_range_report

    sv_params = convert_param_base64(fg, symbol, date1, daysout, years)
    print('years=', years)
    print(sv_params)

    thumbnail_path, thumbnail_url = create_socialmedia_thumbnail('fb', int(fg), date1, symbol, daysout, dir, avg_p, years, title_pre, category)

    page_id = FACEBOOK_PAGE_ID
    image_url = thumbnail_url
    sv_path = config.domain_root + 'wave-viewer?o=' + sv_params

    market = config.available_resources[fg]
    ticker = symbol
    prob_years = years
    avg_gain = avg_p
    days = daysout
    report_link = slug
    top10_url = config.domain_root + 'top10/'
    open_in_wave_viewer_link = sv_path

    title, p1, p2 = get_random_facebook_content(
        market, company, ticker, date1, date2, years, prob_years, avg_gain, days, report_link, open_in_wave_viewer_link, top10_url
    )

    message = title + p1 + p2
    message = textwrap.dedent(message)

    post_id, action = create_facebook_post(page_id, message, image_url)
    return post_id, action
# ------------------------------------------------------------------------------------------------


# this adds one of afshin's auto posts
def post_one_of_am_dr_reports(action_dict):
    fg = int(action_dict['id'])
    date1 = action_dict['date']
    days_hold = int(action_dict['days_hold'])
    symbol = action_dict['symbol']
    years = action_dict['years']
    userid = action_dict['userid']
    post_slug = action_dict['slug']
    dir = action_dict['dir']
    sharpe_ratio = action_dict['sharpe_ratio']
    note = action_dict['note']
    request_datetime = action_dict['request_datetime']

    title_pre = f'{years}-Year '
    date2 = inc_date_day(date1, int(days_hold))

    keyprovider_token = get_keyprovider_token()
    appserver_token = login_appserver(keyprovider_token)
    r = get_chart_data(action_dict['id'], date1, symbol, action_dict['days_hold'], years, appserver_token)
    avg_p = r['stats']['Avg Profit']

    top10 = top10_link(fg)
    sv_params = convert_param_base64(fg, symbol, date1, days_hold, years)
    sv_path = config.domain_root + 'wave-viewer?o=' + sv_params

    base_year = str(datetime.datetime.now().year)
    subfolders = f'{base_year}/{date1}/'

    thumbnail_path, thumbnail_url = create_socialmedia_thumbnail('fb', int(fg), date1, symbol, days_hold, dir, avg_p, years, title_pre, config.category_date_range_report)

    image_url = thumbnail_url

    if note == '' or note == '-':
        message = f"""
        This {days_hold}-day Wave Opportunity is part of {config.available_resources[str(fg)]}
        Symbol: {symbol} Opportunity Date Range: {date1} to {date2} 
        Full Report: {config.domain_root}{post_slug}
        Wave Viewer: {sv_path}
        Top 10 Today: {top10}
        """
    else:
        message = f"""
        {note}
        This {days_hold}-day Wave Opportunity is part of {config.available_resources[str(fg)]}
        Symbol: {symbol} Opportunity Date Range: {date1} to {date2} 
        Full Report: {config.domain_root}{post_slug}
        Seasonal Viewer: {sv_path}
        Top 10: {top10}
        """

    message = textwrap.dedent(message)

    page_id = FACEBOOK_PAGE_ID
    post_id, action = create_facebook_post(page_id, message, image_url, 'dr')
    print(post_id, action)
# ------------------------------------------------------------------------------------------------


# this deletes one of afshin's auto posts
def del_one_of_am_dr_reports(action_dict):
    post_slug = action_dict['slug']

    post_id = ''
    minutes_passed = 9999999
    file_exists = os.path.exists(config.fb_current_social_media_posts)
    if file_exists:
        with open(config.fb_current_social_media_posts, 'r') as f:
            post_list = json.load(f)
        for p in post_list:
            img_url = p.get('img_url', '')
            last_slash_idx = img_url.rfind('/')
            found_slug = img_url[last_slash_idx + 1:-4] if last_slash_idx != -1 else ''
            print('del_one_of', post_slug, found_slug)
            if post_slug == found_slug:
                fb_post_datetime = p.get('datetime')
                try:
                    post_datetime = datetime.datetime.strptime(fb_post_datetime, '%Y-%m-%d %H:%M:%S')
                    current_datetime = datetime.datetime.now()
                    minutes_passed = (current_datetime - post_datetime).total_seconds() / 60
                except Exception:
                    minutes_passed = 9999999
                post_id = p.get('post_id') or p.get('photo_id')
                break

    if post_id and minutes_passed < getattr(config, 'minutes_before_fb_post_is_locked', 60):
        delete_facebook_post(post_id)
# ------------------------------------------------------------------------------------------------


def delete_all(type):  # delete all posts of a given type from our local log and FB
    if not os.path.exists(config.fb_current_social_media_posts):
        return
    with open(config.fb_current_social_media_posts, 'r') as f:
        post_list = json.load(f)

    for p in list(post_list):
        if p.get('type') == type:
            pid = p.get('post_id') or p.get('photo_id')
            if pid:
                delete_facebook_post(pid)


# ------------------------------------------------------------------------------------------------
# get last 10 symbols posted from the json logs
def get_last_10_symbols_posted():
    symbol_list = []
    if os.path.exists(config.fb_current_social_media_posts):
        prev_posts = json_log(config.fb_current_social_media_posts, 'get', {})
        for i in range(len(prev_posts) - 1, 0, -1):
            print('i=', i)
            message = prev_posts[i].get('message', '')
            if '(' not in message:
                continue
            i1 = message.index('(') + 1
            i2 = message.index(')')
            symbol = message[i1:i2].strip()
            symbol_list.append(symbol)
    print(symbol_list)
    return symbol_list
# ------------------------------------------------------------------------------------------------


def find_random_seasonal_for_facebook():
    filename = config.today_top10_data
    print(filename)
    action, dfd = load_top10(filename)

    posted_symbols = get_last_10_symbols_posted()

    fg_lst, i_lst, sym_lst, ap_lst, sr_lst = [], [], [], [], []

    for fg in list(config.available_resources.keys()):
        if int(fg) > 4 and int(fg) != 11:
            continue  # only stocks and ETF per original logic
        print(fg, config.available_resources[fg])
        fgi = int(fg)
        num_opps = dfd[fgi].shape[0]
        if num_opps > 5:
            num_opps = 5

        for i in range(num_opps):
            symbol = dfd[fgi].iloc[i]['Symbol']
            avg_profit = dfd[fgi].iloc[i]['Avg Profit']
            sr = dfd[fgi].iloc[i]['Sharpe Ratio']
            fg_lst.append(fgi)
            i_lst.append(i)
            sym_lst.append(symbol)
            ap_lst.append(avg_profit)
            sr_lst.append(sr)

    df = pd.DataFrame()
    df['fg'] = fg_lst
    df['i'] = i_lst
    df['symbol'] = sym_lst
    df['avg_profit'] = ap_lst
    df['sharpe ratio'] = sr_lst

    df['avg_profit2'] = df['avg_profit'].str.rstrip('%').astype(float)

    df = df.sort_values(by=['avg_profit2'], ascending=False)
    df = df.drop_duplicates(subset=['symbol', 'avg_profit'])
    df = df[df['avg_profit2'] > 10]  # only consider if avg profit > 10%

    num_rows = df.shape[0]
    print('df_rows=', num_rows)

    if num_rows == 0:
        # fallback to first group and first row if filters emptied everything
        # prevents crash if top10 happens to be all below threshold that day
        fgi = 0
        return dfd, fgi, 0

    for _ in range(0, 10):  # try up to 10 times to find a unique symbol
        row_num = random.randint(0, num_rows - 1)
        fg = int(df['fg'].iloc[row_num])
        r = int(df['i'].iloc[row_num])
        symbol = df['symbol'].iloc[row_num]
        if symbol not in posted_symbols:
            print('found symbol:', symbol)
            return dfd, fg, r

    # if we could not find a unique symbol, just return the best one
    row_num = 0
    fg = int(df['fg'].iloc[row_num])
    r = int(df['i'].iloc[row_num])
    return dfd, fg, r
# ------------------------------------------------------------------------------------------------


#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':
    # delete_facebook_post('281345001326382')
    # delete_all('seasonal')
    # exit()

    dfd, fg, r = find_random_seasonal_for_facebook()
    hashtags = '#FinancialAnalyst #MarketAnalysis #traders #investors #Stocks'

    post_id, action = post_one_of_top10(dfd[fg], str(fg), r, hashtags)
    print(post_id, action)

    if action == 'exist':
        # delete the existing same-day feed story, then recreate a new one
        delete_facebook_post(post_id)
        dfd, fg, r = find_random_seasonal_for_facebook()
        post_id, action = post_one_of_top10(dfd[fg], str(fg), r, hashtags)
        print('recreation: ', post_id, action)
