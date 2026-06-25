# blog_service will generate a date range report - tasks are pushed into a redis queue 
# the process routine will then read each task and run create_report function to generated the report
# 
# process will pop each opp_dict from the queue and
# 1- create a report for the date range
# 2- add the report to the user list
# 3- add the free user reports to the free users running list
# 4- add to the logs on logcollector for the requesting user 
# 5- send a notification email to the report initiator

from flask import Flask, jsonify, request
import os
import time
import redis
import json
# from create_report import generate_report
import datetime
import logging
from email_tools import process_user_email_service, delete_from_mailerlite

from article_images import create_article_images
from create_report import get_chart_data, get_keyprovider_token, login_appserver
from blog_tools import get_company_name
from article_prompt import create_article_prompt, get_opp_data

from publish_article import publish_article_web,load_article_from_folder,delete_article_web
import sys
sys.path.insert(0, '/home/flask')
import config  # exposes config.available_resources
stream_name = 'date_range_opp_queue'


# redis = redis.Redis(host='localhost', port=6379, db=1)
# redis_client = redis.Redis(host='localhost', port=6379, db=0)
redis_client = redis.Redis(host='localhost', port=6379, db=0)
redis_client3 = redis.Redis(host='localhost', port=6379, db=config.articles_redis_db) # used for news article queue

logging.basicConfig(filename="debug.log",level=logging.DEBUG,format="%(asctime)s %(message)s")

app = Flask (__name__)



def inject_hero_into_article(article_html, hero_html):
    # If a hero is already present, do not inject another one
    if '<figure class="hero"' in article_html:
        return article_html

    if not hero_html or not hero_html.strip():
        return article_html

    marker = '<p class="dek">'
    idx = article_html.find(marker)
    if idx == -1:
        return hero_html + article_html

    close_p = article_html.find('</p>', idx)
    if close_p == -1:
        return hero_html + article_html

    insert_pos = close_p + len('</p>')
    return article_html[:insert_pos] + '\n' + hero_html + article_html[insert_pos:]

#--------------------------------------------------------------------------------------------------------------------------------------------------------------
@app.route('/')
def home():
    ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
    return jsonify({'message' : 'Seasonal Report Generator queue version 1.0 - ip:'+ip ,'length':len(ip) })
#--------------------------------------------------------------------------------------------------------------------------------------------------------------
# appserver creates 2 csvs daily with crontab and sends it to webserver for consumption by articles and other features like volume spike list 
#--------------------------------------------------------------------------------------------------------------------------------------------------------------
@app.route("/update_volume_lists", methods=["POST"])
def update_volume_lists():
    data = request.get_json(force=True)

    hv = data.get("highest_volume", [])
    hs = data.get("highest_spikes", [])

    system_list_dir = "/home/flask/blog/volume_lists"
    os.makedirs(system_list_dir, exist_ok=True)

    # highest_volume_list.csv
    hv_path = os.path.join(system_list_dir, "highest_volume_list.csv")
    with open(hv_path, "w") as f:
        f.write("rank,ticker,avg_volume_30d,today_volume,rvol\n")
        for row in hv:
            f.write(",".join(str(v) for v in row) + "\n")

    # highest_volume_spikes.csv
    hs_path = os.path.join(system_list_dir, "highest_volume_spikes.csv")
    with open(hs_path, "w") as f:
        f.write("rank,ticker,avg_volume_30d,today_volume,rvol\n")
        for row in hs:
            f.write(",".join(str(v) for v in row) + "\n")

    return jsonify({"status": "ok"})
#--------------------------------------------------------------------------------------------------------------------------------------------------------------
# this is not a part of queue of actions for blogs instead its for processing mailerlite info when new user is registered or is updated subscription 
# this thing processes new user being added or modified by adding or updating records in  emailservie which is mailerlite on  based on ump data 9/6/2023
@app.route('/user_deleted/<int:userid>/<string:email>', methods=['GET'])
def user_deleted(userid,email):
    #-------------------------------------------------------------------------------------
    # don't need userid from wordpress but keeping it for possible fututre need
    #-------------------------------------------------------------------------------------

    print('userid for deleted=',userid)
    print('email=',email)

    response=delete_from_mailerlite(email)        

    print(response)

    return jsonify({'message': 'success'})
#--------------------------------------------------------------------------------------------------------------------------------------------------------------
# this is not a part of queue of actions for blogs instead its for processing mailerlite info when new user is registered or is updated subscription 
# this thing processes new user being added or modified by adding or updating records in  emailservie which is mailerlite on  based on ump data 9/6/2023
@app.route('/user_process/<int:userid>', methods=['GET'])
def user_process(userid):
    #-------------------------------------------------------------------------------------
    # process_user_email_service is in email_tools.py
    # 0 - get user information from UMP
    # 1 - check if user is in mailerlite
    # 2 - add new user to mailerlite or modify existing user in mailerlite
    # 3 - add / modify groups for the user in mailerlite 
    #-------------------------------------------------------------------------------------

    # used it for debug
    # msg = f'user id being processed is {userid}\n'
    # with open("/home/flask/blog/logs/example.txt", "a") as file:    file.write(msg)
        
    process_user_email_service(userid)

    return jsonify({'message': 'success'})

#--------------------------------------------------------------------------------------------
# opp that comes in should be added to redis stream db=2
@app.route('/report/<string:resourceID>/<string:symbol>/<string:date>/<string:days_hold>/<string:years>/<string:base_year>/<string:zero_last_year>/<string:category>/<string:userid>/<string:user_level>/<string:title>/<string:slug>', methods=['GET'])
def report(resourceID, symbol, date, days_hold, years, base_year, zero_last_year, category,userid,user_level,title,slug):
    #-------------------------------------------------------------------------------------
    # check if user has used up the alotted number of reports for the day
    #-------------------------------------------------------------------------------------


    action_dict = {
        'action': 'seasonal_report',
        'id': resourceID,
        'symbol': symbol,
        'date': date,
        'days_hold': days_hold,
        'years': years,
        'base_year': base_year,
        'zero_last_year': zero_last_year,
        'userid': userid,
        'request_datetime': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'),
        'category':category,
        'user_level':user_level ,
        'title':title,
        'slug':slug
    }
    ret=redis_client.rpush(stream_name, json.dumps(action_dict))

    return jsonify({'message': 'success','return':ret})

#-------------------------------------------------------------------------------------------------------
# Async news article generation (full auto, mode 2 only) - places the article on queue for processing
#-------------------------------------------------------------------------------------------------------
@app.route('/write_news_article/<string:resource_id>/<string:symbol>/<string:date>/<string:days>/<string:years>/<string:direction>/<string:userid>/<string:article_publish_date>', methods=['GET'])
def write_news_article(resource_id, symbol, date, days, years, direction, userid, article_publish_date):
    """
    Enqueue or update a fully automated news article generation job.

    Key identity for a queued article:
        (resource_id, symbol, date, days, years, userid)

    Behavior:
        - If a matching job already exists in the queue:
              -> update its article_publish_date (and request_datetime)
        - Else:
              -> push a new job to the queue
    """

    queue_name = config.NEWS_QUEUE_NAME
    now_iso    = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')

    # Normalize key components as strings (for safe comparison with JSON)
    rid    = str(resource_id)
    sym    = str(symbol).upper()
    sdate  = str(date)
    sdays  = str(days)
    syears = str(years)
    sdir   = str(direction)  # long or short
    uid    = str(userid)
    pattern_mode = str(request.args.get('pattern_mode', 'consecutive') or 'consecutive').lower()

    # ------------------------------------------------------------------
    # 1) Scan queue for an existing matching job
    # ------------------------------------------------------------------
    try:
        queue_items = redis_client3.lrange(queue_name, 0, -1)
    except Exception as e:
        print(f"[ERROR] write_news_article: failed to read queue {queue_name}: {e}")
        return jsonify({'message': 'error', 'error': 'redis_read_failed'}), 500

    existing_index = None
    existing_entry = None

    for idx, raw in enumerate(queue_items):
        try:
            entry = json.loads(raw)
        except Exception:
            continue

        # Match on full identity: resource_id + symbol + date + days + years + userid
        if (str(entry.get('resource_id')) == rid and
            str(entry.get('symbol', '')).upper() == sym and
            str(entry.get('date')) == sdate and
            str(entry.get('days')) == sdays and
            str(entry.get('years')) == syears and
            str(entry.get('userid')) == uid):

            existing_index = idx
            existing_entry = entry
            break

    # ------------------------------------------------------------------
    # 2) If exists: update article_publish_date (and request_datetime)
    # ------------------------------------------------------------------
    if existing_index is not None and existing_entry is not None:
        existing_entry['article_publish_date'] = article_publish_date
        existing_entry['request_datetime']     = now_iso  # optional but nice

        try:
            redis_client3.lset(queue_name, existing_index, json.dumps(existing_entry))
        except Exception as e:
            print(f"[ERROR] write_news_article: failed to LSET queue item: {e}")
            return jsonify({'message': 'error', 'error': 'redis_update_failed'}), 500

        return jsonify({
            'message': 'queued',
            'index': existing_index
        })

    # ------------------------------------------------------------------
    # 3) If not exists: push a new job
    # ------------------------------------------------------------------
    action_dict = {
        'action': 'write_news_article',
        'resource_id': rid,
        'symbol': sym,
        'date': sdate,                 # pattern_start_date
        'days': sdays,                 # pattern_window_days
        'years': syears,               # always keep as STRING
        'direction':sdir,              # long or short
        'userid': uid,
        'article_publish_date': article_publish_date,  # YYYY-MM-DD
        'mode': '2',                   # full async article generation
        'pattern_mode': pattern_mode,  # 'pe' or 'consecutive' — drives article_prompt PE formatting
        'request_datetime': now_iso,
    }

    try:
        ret = redis_client3.rpush(queue_name, json.dumps(action_dict))
    except Exception as e:
        print(f"[ERROR] write_news_article: failed to RPUSH queue item: {e}")
        return jsonify({'message': 'error', 'error': 'redis_enqueue_failed'}), 500

    return jsonify({'message': 'queued', 'return': ret})
#--------------------------------------------------------------------------------------------
# am_dr_sm send afshin's dr report to social media for auto post
#---------------------------------------------------------------------------------------------
@app.route('/am_dr_sm/<string:resourceID>/<string:symbol>/<string:date>/<string:days_hold>/<string:years>/<string:userid>/<string:slug>/<string:dir>/<string:sharpe_ratio>/<string:note>', methods=['GET'])
def am_dr_report(resourceID, symbol, date, days_hold, years,userid,slug,dir,sharpe_ratio,note):
    #-------------------------------------------------------------------------------------
    # check if user has used up the alotted number of reports for the day
    #-------------------------------------------------------------------------------------
    action_dict = {
        'action': 'am_dr_sm',
        'id': resourceID,
        'symbol': symbol,
        'date': date,
        'days_hold': days_hold,
        'years': years,
        'userid': userid,
        'slug':slug,
        'dir':dir,
        'sharpe_ratio':sharpe_ratio,
        'note':note,
        'request_datetime': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    ret=redis_client.rpush(stream_name, json.dumps(action_dict))


    return jsonify({'message': 'success','return':ret})
#---------------------------------------------------------------------------------------------
# am_dr_sm delete afshin's dr report to social media for auto post
#---------------------------------------------------------------------------------------------
@app.route('/am_dr_sm_del/<string:resourceID>/<string:symbol>/<string:date>/<string:days_hold>/<string:years>/<string:userid>/<string:slug>/<string:dir>/<string:sharpe_ratio>', methods=['GET'])
def am_dr_sm_del(resourceID, symbol, date, days_hold, years,userid,slug,dir,sharpe_ratio):

    action_dict = {
        'action': 'am_dr_sm_del',
        'id': resourceID,
        'symbol': symbol,
        'date': date,
        'days_hold': days_hold,
        'years': years,
        'userid': userid,
        'slug':slug,
        'dir':dir,
        'sharpe_ratio':sharpe_ratio,
        # 'note':note,
        'request_datetime': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    ret=redis_client.rpush(stream_name, json.dumps(action_dict))


    return jsonify({'message': 'success','return':ret})
#---------------------------------------------------------------------------------------------
# user deleted a report
@app.route('/delreport/<string:userid>/<string:slug>', methods=['GET'])
def delreport(userid,slug):
    #-------------------------------------------------------------------------------------
    # check if user has used up the alotted number of reports for the day
    #-------------------------------------------------------------------------------------
    action_dict = {
        'action':'delreport',
        'userid': userid,
        'slug'  :slug
    }
    ret=redis_client.rpush(stream_name, json.dumps(action_dict))

    return jsonify({'message': 'success','return':ret})
#---------------------------------------------------------------------------------------------
@app.route('/top10pages/<string:date>')
def top10pages(date):

    action_dict = {
        'action':'create_top10_pages',
        'request_datetime': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'),
        'date':date
    }
    redis_client.rpush(stream_name, json.dumps(action_dict))

    return jsonify({'message': 'success'})
#---------------------------------------------------------------------------------------------
@app.route('/opp_list_blog/<string:id>/<string:date>')
def opp_list_blogs(id,date):

    action_dict = {
        'action':'opp_list_blog',
        'id':id,
        'date':date,
        'request_datetime': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    }
    redis_client.rpush(stream_name, json.dumps(action_dict))

    return jsonify({'message': 'success'})
#---------------------------------------------------------------------------------------------
@app.route('/opp_list_blog_w_thumbnails/<string:id>/<string:date>')
def opp_list_blogs_w_thumbnails(id,date):

    action_dict = {
        'action':'opp_list_blog_w_thumbnails',
        'id':id,
        'date':date,
        'request_datetime': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    }
    redis_client.rpush(stream_name, json.dumps(action_dict))

    return jsonify({'message': 'success'})
#---------------------------------------------------------------------------------------------
@app.route('/top10_page_by_date/<string:date>')
def top10_page_by_date(date):

    action_dict = {
        'action':'top10_page_by_date',
        'date':date,
        'request_datetime': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'),
    }
    redis_client.rpush(stream_name, json.dumps(action_dict))

    return jsonify({'message': 'success'})
#---------------------------------------------------------------------------------------------
@app.route('/top10_page_based_on_sr/<string:date>')
def top10_page_based_on_sr(date):

    action_dict = {
        'action':'top10_page_based_on_sr',
        'date':date,
        'request_datetime': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    }
    redis_client.rpush(stream_name, json.dumps(action_dict))

    return jsonify({'message': 'success'})
#---------------------------------------------------------------------------------------------
@app.route('/archive_list')
def archive_list():

    action_dict = {
        'action':'archive_list',
        'request_datetime': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'),
    }
    redis_client.rpush(stream_name, json.dumps(action_dict))

    return jsonify({'message': 'success'})
#---------------------------------------------------------------------------------------------
#  Delete Reports 
#    a) number of days old 
#    b) category
#    c) userid
#    d) invididual reports by slug
#    e) post_id - delete this id
#    f) delete by max_total reports - if reach 1000,000 - the oldest reports are removed
@app.route('/delete/<string:num_days_old>/<string:category>/<string:user_id>/<string:slug>/<string:post_id>/<string:max_total>')
def delete_posts(num_days_old,category,user_id,slug,post_id,max_total):

    action_dict = {
        'action': 'cleanup',
        'num_days_old': num_days_old,
        'category': category,
        'slug': slug,
        'post_id': post_id,
        'user_id': user_id,
        'max_total': max_total,
        'request_datetime': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }



    redis_client.rpush(stream_name, json.dumps(action_dict) )

    return jsonify({'message': 'success'})
#---------------------------------------------------------------------------------------------
# mode can be 
# default - creates chatGPT UI prompt includes web search
# hero    - only recreate the hero
# 0       - same as default
# 1       - perplexity-sonar API for websearch - prompt generated for article generation with no web search
# 2       - full automation - same as 1 except it will also run gpt 5.1 api to generate the article
@app.route('/article_prompt/<string:resource_id>/<string:symbol>/<string:date>/<string:days>/<string:years>/<string:userid>/<string:mode>/<string:note>', methods=['GET'])
def article_prompt(resource_id, symbol, date, days, years, userid, mode, note):
    image_size_key = 'x'
    theme = "light"
    # byline = "Powered by TradeWave.AI"
    byline = "Analysis powered by the TradeWave quantitative engine."
    ai_disclosure = False
    direction = str(request.args.get('direction', 'long') or 'long').lower()
    sentiment = "bullish" if direction == "long" else "bearish" if direction == "short" else "neutral"

    print('blog_queue calling article prompt = ', resource_id, symbol, date, days, years, userid, mode, note)

    # ================= HERO-ONLY MODE =================
    if mode == "hero":
        try:
            from article_hero_image import hero_image_workflow
            hero_info = hero_image_workflow(resource_id=str(resource_id), symbol=symbol, date=date, sentiment=sentiment)

            if hero_info and hero_info.get("image_url"):
                # success, nothing else needed
                return jsonify({
                    "message": "success"
                })
            else:
                return jsonify({
                    "message": "failed",
                    "reason": "No hero image generated or missing URL."
                }), 500

        except Exception as e:
            print(f"[WARN] Hero image generation failed in hero-only mode: {e}")
            return jsonify({
                "message": "failed",
                "reason": str(e)
            }), 500

    # ================= ARTICLE MODES ("0", "1", "2") =================

    # step 1) core charts
    img_paths = create_article_images(image_size_key, resource_id, date, symbol, days, years, theme)

    # step 2) hero (mirror the smoke test behavior)
    hero_html = ""
    try:
        from article_hero_image import hero_image_workflow
        hero_info = hero_image_workflow(resource_id=str(resource_id), symbol=symbol, date=date, sentiment=sentiment)
        if hero_info and hero_info.get("image_url"):
            alt_text = f"{get_company_name(resource_id, symbol) or symbol} ({symbol}) market analysis and seasonal trends - TradeWave.ai"
            from article_hero_image import HERO_WIDTH_ATTR, HERO_HEIGHT_ATTR
            hero_html = f'<figure class="hero"><img src="{hero_info["image_url"]}" width="{HERO_WIDTH_ATTR}" height="{HERO_HEIGHT_ATTR}" alt="{alt_text}"></figure>'
            img_paths.append({
                "variant": "hero",
                "url": hero_info["image_url"],
                "path": hero_info.get("image_path", ""),
                "rel": "",
                "alt": alt_text,
            })
            print(f"[SUCCESS] Hero image generated: {hero_info['image_url']}")
        else:
            print("[WARN] No hero image generated or missing URL.")
    except Exception as e:
        print(f"[WARN] Hero image generation failed: {e}")

    print("HERO_HTML_DEBUG:", hero_html[:200])

    # step 3) opp data (uses your -1 day correction inside get_opp_data)
    cdata = get_opp_data(resource_id, date, symbol, days, years, True)

    # step 4) company
    try:
        company = get_company_name(resource_id, symbol) or symbol
    except Exception:
        company = symbol

    # step 5) template variant index (keep your current default of 1 unless you pass it)
    variant_index = 1

    # 6) Mode "0": single-prompt (research + article in one)
    if mode == "0":
        try:
            article_prompt = create_article_prompt(
                symbol=symbol,
                date=date,
                days=days,
                years=years,
                cdata=cdata,
                img_paths=img_paths,
                company=company,
                resource_id=resource_id,
                variant_index=variant_index,
                byline=byline,
                ai_disclosure=ai_disclosure,
                hero_html=hero_html,
                mode="0",       # Mode "0" creates prompt that includes web research in the prompt
                research=None,  # no research JSON in mode "0"
            )
        except Exception as e:
            print(f"[ERROR] create_article_prompt mode 0 failed: {e}")
            return jsonify({
                "message": "failed",
                "reason": str(e)
            }), 500

        print("\n--- ARTICLE PROMPT (MODE 0) ---")
        return jsonify({'message': 'success', 'prompt': article_prompt})

   #--------------------------------------------------------------------------------
    # 7) Modes "1" and "2": Intelligent Research (Grok + Tavily)
    if mode == "1" or mode == "2":
        from article_prompt import detect_market_family, WHITELISTED_SOURCE_DOMAINS
        import AI_tools
        import json

        try:
            # 1. Detect Context
            market_family, resource_name = detect_market_family(resource_id)
            print(f"\n--- STARTING INTELLIGENT RESEARCH PIPELINE ({market_family}) ---")

            # 2. ASK GROK: "Who is this company?" (The Smart Domain Step)
            print(f"🧠 [Grok] Identifying official data sources for {company}...")
            # Note: Ensure get_company_domains_with_grok is in AI_tools.py
            specific_company_domains = AI_tools.get_company_domains_with_grok(symbol, company)
            print(f"✅ [Grok] Identified official domains: {specific_company_domains}")

            # 3. MERGE LISTS: Master Whitelist + Company Specifics
            # We use the imported WHITELISTED_SOURCE_DOMAINS as the master list
            dynamic_whitelist = list(set(WHITELISTED_SOURCE_DOMAINS + specific_company_domains))

            # 4. TAVILY SEARCH (The "Double Tap" Strategy for Volume)
            # Query A: General News & Earnings
            query_a = f"{company} ({symbol}) stock price news earnings analyst ratings"
            # Query B: Special Signals & Insider Data
            query_b = f"{company} ({symbol}) insider trading unusual options short interest technical analysis"
            
            print(f"🕵️ [Tavily] Running 'Double Tap' search with {len(dynamic_whitelist)} trusted domains...")
            
            # print('tttttttttttttttttavily query_a=',query_a)
            # print('tttttttttttttttttavily query_b=',query_b)

            # Run two searches to ensure we get enough sources
            resp_a = AI_tools.search_tavily(query=query_a, include_domains=dynamic_whitelist, days=365)
            resp_b = AI_tools.search_tavily(query=query_b, include_domains=dynamic_whitelist, days=365)
            
            # Merge results (Deduplication handled by AI_tools.format_tavily_results implicitly via text)
            combined_results = resp_a.get('results', []) + resp_b.get('results', [])
            
            # 5. FORMAT RESULTS
            # We construct a synthetic response dict to pass to the formatter
            tavily_resp = {"results": combined_results}
            raw_context_text = AI_tools.format_tavily_results(tavily_resp)
            
            count = len(combined_results)
            print(f"✅ [Tavily] Retrieved {count} high-quality sources (Combined).")

            # Fallback: If strict whitelist yields 0 results (very rare), try broader search
            if count == 0:
                print("[WARN] Strict search returned 0 results. Retrying with open search...")
                resp_a = AI_tools.search_tavily(query=query_a, include_domains=None, days=365)
                resp_b = AI_tools.search_tavily(query=query_b, include_domains=None, days=365)
                combined_results = resp_a.get('results', []) + resp_b.get('results', [])
                raw_context_text = AI_tools.format_tavily_results({"results": combined_results})

            # 6. DEFINE FULL SCHEMA 
            # (Matches article_prompt requirements: source_ids, distinct fields)
            target_schema = f"""
            {{
              "symbol": "{symbol}",
              "company": "{company}",
              "market_family": "{market_family}",

              "price": {{
                "last": null,
                "change_percent": null,
                "ytd_percent": null,
                "range_52w_high": null,
                "range_52w_low": null
              }},

              "catalysts": [
                {{
                  "type": "earnings/product/regulation/macro",
                  "date": "YYYY-MM-DD or null",
                  "headline": "Short headline",
                  "summary": "1-2 sentences on impact.",
                  "source_id": 1
                }}
              ],

              "earnings": {{
                "next_earnings_date": "YYYY-MM-DD or null",
                "fiscal_period": "e.g., Q3 2025",
                "recent_results": "Revenue/EPS vs est & key quotes.",
                "guidance": "Forward guidance summary.",
                "sources": []
              }},

              "analyst": {{
                "consensus_rating": "Buy/Hold/Sell",
                "price_target_consensus": null,
                "provider": "e.g. FactSet via CNBC",
                "sources": []
              }},

              "special_signals": {{
                "unusual_options": {{ "summary": "Search text for 'unusual options'. If none, null.", "source_id": null }},
                "insider_activity": {{ "summary": "Search text for 'insider trading'. If none, null.", "source_id": null }},
                "volume_spike": {{ "summary": "Search text for 'high volume'. If none, null.", "source_id": null }},
                "short_interest_change": {{ "summary": "Search text for 'short interest'. If none, null.", "source_id": null }}
              }},

              "macro": [ {{ "theme": "string", "summary": "string", "source_id": null }} ],
              "sector": [ {{ "theme": "string", "summary": "string", "source_id": null }} ],
              
              "equity_etf_index": {{ "etf_flows": [], "index_context": [] }},
              "futures_commodities": {{ "term_structure": {{}}, "positioning": {{}}, "inventory": {{}} }},

              "sources": [
                {{
                  "id": 1,
                  "publisher": "Name",
                  "title": "Title",
                  "url": "https://...",
                  "date": "YYYY-MM-DD",
                  "domain_tier": "1",
                  "justification": "Why this source is trusted."
                }}
              ]
            }}
            """


            # print('raw_text_context=',raw_context_text)
            # return({})


            # 7. GROK SYNTHESIS
            print(f"🧠 [Grok] Synthesizing Research JSON...")
            research_json_str = AI_tools.synthesize_research_with_grok(
                raw_text_context=raw_context_text,
                json_schema_str=target_schema,
                symbol=symbol,
                company=company,
            )

            
            # 8. PARSE & VALIDATE
            import re as _re
            clean = research_json_str.replace("```json", "").replace("```", "").strip()
            try:
                research_json = json.loads(clean)
            except json.JSONDecodeError:
                m = _re.search(r'\{.*\}', clean, _re.DOTALL)
                if m:
                    research_json = json.loads(m.group(0))
                else:
                    raise ValueError(f"Grok returned unparseable research JSON: {clean[:200]}")
            print("RESEARCH JSON KEYS:", list(research_json.keys()))
            print('\n', '######################################################################', '\n')

            # 9. GENERATE ARTICLE PROMPT
            article_prompt = create_article_prompt(
                symbol=symbol,
                date=date,
                days=days,
                years=years,
                cdata=cdata,
                img_paths=img_paths,
                company=company,
                resource_id=resource_id,
                variant_index=variant_index,
                byline=byline,
                ai_disclosure=ai_disclosure,
                hero_html=hero_html,
                mode=mode,
                research=research_json,
            )

        except Exception as e:
            print(f"[ERROR] Intelligent Research Pipeline failed: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({"message": "failed", "reason": str(e)}), 500

        # --------- RETURN -----------
        if mode == "1":
            print(f"\n--- ARTICLE PROMPT (WITH TAVILY RESEARCH, MODE 1) ---")
            return jsonify({'message': 'success', 'prompt': article_prompt})    

        if mode == "2":
            try:
                print("\n--- CALLING GPT-5.1 API FOR ARTICLE HTML (MODE 2) ---")
                article_html = AI_tools.send_openai_prompt(
                    article_prompt,
                    system=None,
                    stream=False,
                    temperature=0.0
                )
                # NEW: force hero into the final HTML
                article_html = inject_hero_into_article(article_html, hero_html)

                # SEO title optimization
                try:
                    from article_title import generate_unique_seo_title
                    pattern = {
                        'resource_id': resource_id,
                        'symbol': symbol,
                        'start_date': date,
                        'days': days,
                        'years': years,
                        'company': company,
                    }
                    new_title = generate_unique_seo_title(pattern, article_html, tavily=research_json, persist=True)
                    print(f"[SEO TITLE] Generated: {new_title}")
                    import re as _re2
                    article_html = _re2.sub(r'<title>.*?</title>', f'<title>{new_title}</title>', article_html, count=1, flags=_re2.IGNORECASE | _re2.DOTALL)
                    article_html = _re2.sub(r'<h1[^>]*>.*?</h1>', f'<h1>{new_title}</h1>', article_html, count=1, flags=_re2.IGNORECASE | _re2.DOTALL)
                except Exception as seo_e:
                    print(f"[WARN] SEO title generation failed (non-fatal): {seo_e}")

            except Exception as e:
                print(f"[ERROR] OpenAI article generation failed (Mode 2): {e}")
                return jsonify({
                    "message": "failed",
                    "reason": f"OpenAI article generation failed: {e}"
                }), 500

            print("\n--- ARTICLE HTML (MODE 2, GPT-5.1) GENERATED SUCCESSFULLY ---")
            return jsonify({
                "message": "success",
                "article_html": article_html
            })
            
        return jsonify({"message": "failed", "reason": "Unexpected mode"}), 500
#---------------------------------------------------------------------------------------------
@app.route('/article_publish_bq', methods=['POST'])
def article_publish_bq(): # bq stands for blog_queue which is this script

    print('article_publish route in blog_queue.py just got invoked from the appserver')

    """Publish an article to WordPress via POST payload."""

    payload = request.get_json(silent=True) or {}

    
    if not payload:
        payload = request.form.to_dict()


    # print('payload=',payload)

    required_fields = ['resource_id', 'symbol', 'date', 'days', 'years', 'direction', 'userid', 'article_html']
    missing = [field for field in required_fields if not payload.get(field)]
    if missing:
        message = f"Missing required field(s): {', '.join(missing)}"
        logging.warning("article_publish invalid payload: %s", message)
        return jsonify({'message': message}), 400


    return_dict = publish_article_web(
        str(payload['resource_id']),
        str(payload['symbol']),
        str(payload['date']),
        str(payload['days']),
        str(payload['years']),
        str(payload['direction']),
        str(payload['userid']),
        payload['article_html'],
    )


    # return is in the form 
    # return {
    #     "file_path": str(out_path),
    #     "url": rel_url,
    #     "posts_json": str(posts_json),
    #     "search_index_json": str(search_index_path)
    # }


    return jsonify(return_dict)
#---------------------------------------------------------------------------------------------
@app.route('/delete_article_bq', methods=['POST'])
def delete_article_bq():
    """
    Synchronously delete an article for a given pattern.

    Invariants:
      - A pattern is either:
          (a) queued for generation, or
          (b) already has a published article, or
          (c) neither.
        It is NEVER both queued and published at the same time.

    Behavior:
      1) First, try to remove any matching job from the news queue.
         If found -> remove it and RETURN immediately with "deleted_from_queue".
      2) If not found in the queue, attempt to delete the published article.
         Return "deleted_article" (or whatever delete_article_web reports).
    """

    payload = request.get_json(silent=False) or {}
    print("\n[delete_article_bq] payload =", payload)

    required_fields = ['resource_id', 'symbol', 'date', 'days', 'years', 'userid']
    missing = [f for f in required_fields if f not in payload]
    if missing:
        msg = f"Missing required field(s): {', '.join(missing)}"
        print("[delete_article_bq] ERROR:", msg)
        return jsonify({"message": "error", "reason": msg}), 400

    rid   = str(payload['resource_id'])
    sym   = str(payload['symbol']).upper()
    date = str(payload['date'])
    days = str(payload['days'])
    years = str(payload['years'])
    uid   = str(payload['userid'])

    # ------------------------------------------------------------------
    # 1) FIRST: try to remove from the news article queue
    # ------------------------------------------------------------------
    queue_name  = config.NEWS_QUEUE_NAME
    queue_items = redis_client3.lrange(queue_name, 0, -1)

    target_raw = None
    for raw in queue_items:
        try:
            entry = json.loads(raw)
        except Exception as e:
            print("[delete_article_bq] JSON decode error in queue:", e)
            continue

        if (str(entry.get('resource_id')) == rid and
            str(entry.get('symbol', '')).upper() == sym and
            str(entry.get('date')) == date and
            str(entry.get('days')) == days and
            str(entry.get('years')) == years):

            target_raw = raw
            break

    if target_raw is not None:
        removed_count = redis_client3.lrem(queue_name, 1, target_raw)
        print(f"[delete_article_bq] deleted from queue: removed_count={removed_count}")
        # Invariant says: if it was queued, it was NOT published yet
        return jsonify({
            "message": "deleted_from_queue",
            "removed_from_queue": int(removed_count),
        })

    # ------------------------------------------------------------------
    # 2) Not queued -> delete the published article, if it exists
    # ------------------------------------------------------------------
    try:
        # You already have (or will have) this function wired to:
        #   - remove HTML / file
        #   - update any Redis keys / WordPress / etc.
        delete_result = delete_article_web( rid,sym,date,days,years,uid)
    except Exception as e:
        print("[delete_article_bq] ERROR during delete_article_web:", e)
        return jsonify({
            "message": "error",
            "reason": str(e),
        }), 500

    out = {
        "message": "deleted_article",
    }

    # If delete_article_web returns extra info, include it
    if isinstance(delete_result, dict):
        out.update(delete_result)

    print("[delete_article_bq] SUCCESS:", out)
    return jsonify(out)
#---------------------------------------------------------------------------------------------
@app.route('/article_load_bq/<string:resource_id>/<string:symbol>/<string:date>/<string:days>/<string:years>/<string:userid>', methods=['GET'])
def article_load_bq(resource_id, symbol, date, days, years, userid): # bq stands for blog_queue which is this script

    print('article_load_bq route in blog_queue.py just got invoked from the appserver')

    article_data = load_article_from_folder(resource_id, symbol, date, days, years, userid)

    # print('article_data=',article_data)

    # Safety check: if the loader ever returns something unexpected
    if not isinstance(article_data, dict):
        logging.error(f"load_article_from_folder returned non-dict: {article_data}")
        return jsonify({
            "found": False,
            "reason": "invalid response from load_article_from_folder",
            "html": None,
        }), 500

    # If you prefer always-200, change this to `status_code = 200`
    status_code = 200 if article_data.get("found") else 404

    return jsonify(article_data), status_code
#---------------------------------------------------------------------------------------------
# @app.route('/get_news_home_url/', methods=['GET'])
# def get_news_home_url(): # just returns news home url to appserver and then UI
#     root = config.domain_root.rstrip("/")
#     sub  = config.articles_subfolder.strip("/")
#     if sub:
#         full = f"{root}/{sub}/"
#     else:
#         full = f"{root}/"
#     return jsonify({"news_home_url": full})
@app.route('/get_news_home_url/', methods=['GET'])
def get_news_home_url(): # just returns news home url to appserver and then UI
    root = config.news_website_url.rstrip("/")
    full = f"{root}/"
    return jsonify({"news_home_url": full})
###########################################################################################################
if __name__ == "__main__":
    # app.run(host='0.0.0.0',debug=True, port=7171) # port 5001 is only for dev during debug - remove for prod
    # app.run(host='10.0.0.81',debug=True, port=7171) 
    app.run(host='0.0.0.0',debug=True, port=5001) 



# ---------------------------------------------------------------------------
# article_status: read-only status for a saved pattern's article. Lets the
# appserver's dr_report_list (article toggle) read article_exists/queued/metadata
# over HTTP instead of reaching this box's Redis db3 directly (redis is local-only
# here). Mirrors the lookups dr_report_list used to do. Added 2026-05-27.
# ---------------------------------------------------------------------------
@app.route('/article_status/<string:resource_id>/<string:symbol>/<string:date>/<string:days>/<string:years>', methods=['GET'])
def article_status(resource_id, symbol, date, days, years):
    rid    = str(resource_id)
    sym    = str(symbol).upper()
    sdate  = str(date)
    sdays  = str(days)
    syears = str(years)
    out = {'article_exists': False, 'article_queued': False, 'article_publish_date': None,
           'has_article': False, 'article_title': None, 'article_url': None,
           'article_dek': None, 'article_published': None, 'article_tone': None,
           'article_website_id': None}
    # queued? scan the news_article_queue for a matching job
    try:
        for raw in redis_client3.lrange(config.NEWS_QUEUE_NAME, 0, -1):
            try:
                entry = json.loads(raw)
            except Exception:
                continue
            if (str(entry.get('resource_id')) == rid and
                str(entry.get('symbol', '')).upper() == sym and
                str(entry.get('date')) == sdate and
                str(entry.get('days')) == sdays and
                str(entry.get('years')) == syears):
                out['article_queued'] = True
                out['article_publish_date'] = entry.get('article_publish_date')
                break
    except Exception as e:
        print(f"[article_status] queue scan failed: {e}")
    # exists + metadata: the published-article record (tone=neutral, website_id=0)
    redis_key = f"{rid}_{sym}_{sdate}_{sdays}_{syears}_neutral_0"
    try:
        raw = redis_client3.get(redis_key)
    except Exception as e:
        print(f"[article_status] get failed: {e}")
        raw = None
    if raw:
        out['article_exists'] = True
        try:
            payload = json.loads(raw)
            entry = payload.get('entry', {}) or {}
            out['has_article'] = True
            out['article_title'] = entry.get('title')
            out['article_url'] = entry.get('url')
            out['article_dek'] = entry.get('dek')
            out['article_published'] = entry.get('published_date')
            out['article_tone'] = payload.get('tone')
            out['article_website_id'] = payload.get('website_id')
        except Exception as e:
            print(f"[article_status] decode failed: {e}")
    return jsonify(out)
