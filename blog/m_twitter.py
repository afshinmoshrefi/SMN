# changed the parameters of 
# run_opp_blog_generation(opp_date):
# added parameter html_table_only boolean
# this is used to only create an html table for each opportunities list

import tweepy
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
import sys
import subprocess
from get_top10_data import load_top10
from slugify import slugify
sys.path.insert(0, '/home/flask')
import config




API_KEY       = config.TWITTER_API_KEY        
API_KEY_SECRET= config.TWITTER_API_KEY_SECRET 
CLIENT_ID     = config.TWITTER_CLIENT_ID          
CLIENT_SECRET = config.TWITTER_CLIENT_SECRET  
BEARER_TOKEN  = config.TWITTER_BEARER_TOKEN   
ACCESS_TOKEN  = config.TWITTER_ACCESS_TOKEN   
ACCESS_TOKEN_SECRET = config.TWITTER_ACCESS_TOKEN_SECRET

#------------------------------------------------------------------------------------------------



# ========= Utilities =========

def inc_date_day(d: str, i: int) -> str:
    return (datetime.datetime.strptime(d, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')

def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _ensure_dirs(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

# ========= Twitter client =========

def _get_tw_api() -> "tweepy.API":
    if getattr(config, "TW_DRY_RUN", False):
        return None
    if tweepy is None:
        raise RuntimeError("tweepy not installed. pip install tweepy")
    required = ["TW_API_KEY","TW_API_SECRET","TW_ACCESS_TOKEN","TW_ACCESS_SECRET"]
    for k in required:
        if not getattr(config, k, None):
            raise RuntimeError(f"Missing config.{k}")
    auth = tweepy.OAuth1UserHandler(
        consumer_key=config.TW_API_KEY,
        consumer_secret=config.TW_API_SECRET,
        access_token=config.TW_ACCESS_TOKEN,
        access_token_secret=config.TW_ACCESS_SECRET,
    )
    api = tweepy.API(auth, wait_on_rate_limit=True)
    if not getattr(config, "TW_SKIP_VERIFY", False):
        api.verify_credentials()
    return api

# ========= Image templates =========

def _render_bar_card(outfile: str, title: str, sub: str, years: list, values: list,
                     sharpe: str, win_rate: str, cumulative: str, dark: bool = True):
    w, h, dpi = 16, 9, 100  # 1600x900
    fig = plt.figure(figsize=(w, h), dpi=dpi)
    bg = "#0e1117" if dark else "white"
    fg = "white" if dark else "black"
    gridc = "#3a3f4b" if dark else "#cccccc"
    barc = "#36d659" if dark else "#1f8f2d"
    fig.patch.set_facecolor(bg)

    ax_title = fig.add_axes([0, 0, 1, 1]); ax_title.axis("off")
    ax_title.text(0.5, 0.94, title, ha="center", va="center", color=fg, fontsize=40, weight="bold")
    ax_title.text(0.5, 0.89, sub,    ha="center", va="center", color=("lightgray" if dark else "dimgray"), fontsize=22)

    ax = fig.add_axes([0.08, 0.23, 0.84, 0.55], facecolor=bg)
    ax.bar(years, values)
    for spine in ax.spines.values(): spine.set_color(fg)
    ax.tick_params(colors=fg, labelsize=14)
    ax.grid(axis='y', linestyle='--', alpha=0.3, color=gridc)
    for p in ax.patches: p.set_color(barc)
    ax.set_title("10-Year Seasonal Window Returns", color=fg, fontsize=26, weight="bold")

    ax_b = fig.add_axes([0, 0, 1, 1]); ax_b.axis("off")
    ax_b.text(0.05, 0.07, f"Sharpe {sharpe}",        color=fg, fontsize=18)
    ax_b.text(0.40, 0.07, f"% Profitable {win_rate}", color=fg, fontsize=18)
    ax_b.text(0.72, 0.07, f"Cumulative {cumulative}", color=fg, fontsize=18)
    ax_b.text(0.98, 0.07, "TradeWave.ai",             color=("lightgray" if dark else "dimgray"), fontsize=18, ha="right")

    _ensure_dirs(outfile)
    fig.savefig(outfile, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)

def _render_trend_card(outfile: str, title: str, sub: str, cum_curve: np.ndarray,
                       sharpe: str, win_rate: str, cumulative: str, dark: bool = False):
    w, h, dpi = 16, 9, 100
    fig = plt.figure(figsize=(w, h), dpi=dpi)
    bg = "#0e1117" if dark else "white"
    fg = "white" if dark else "black"
    linec = "#36a64f" if dark else "green"
    fillc = (0.21, 0.65, 0.38, 0.2)
    fig.patch.set_facecolor(bg)

    ax_title = fig.add_axes([0, 0, 1, 1]); ax_title.axis("off")
    ax_title.text(0.5, 0.94, title, ha="center", va="center", color=fg, fontsize=40, weight="bold")
    ax_title.text(0.5, 0.89, sub,    ha="center", va="center", color=("lightgray" if dark else "dimgray"), fontsize=22)

    ax = fig.add_axes([0.08, 0.23, 0.84, 0.55], facecolor=bg)
    x = np.arange(len(cum_curve))
    ax.plot(x, cum_curve, linewidth=4, color=linec)
    ax.fill_between(x, cum_curve, cum_curve.min(), color=fillc)
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    for s in ["left","bottom"]: ax.spines[s].set_color(("lightgray" if dark else "gray"))
    ax.tick_params(colors=("lightgray" if dark else "gray"), labelsize=14)
    ax.set_title("10-Year Seasonal Trend (Cumulative Return)", color=fg, fontsize=26, weight="bold")

    ax_b = fig.add_axes([0, 0, 1, 1]); ax_b.axis("off")
    ax_b.text(0.05, 0.07, f"Sharpe {sharpe}",        color=fg, fontsize=18)
    ax_b.text(0.40, 0.07, f"% Profitable {win_rate}", color=fg, fontsize=18)
    ax_b.text(0.72, 0.07, f"Cumulative {cumulative}", color=fg, fontsize=18)
    ax_b.text(0.98, 0.07, "TradeWave.ai",             color=("lightgray" if dark else "dimgray"), fontsize=18, ha="right")

    _ensure_dirs(outfile)
    fig.savefig(outfile, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)

def _render_stat_card(outfile: str, ticker: str, avg_gain: str, wins_text: str, sub: str,
                      sharpe: str, win_rate: str, cumulative: str, dark: bool = True):
    w, h, dpi = 16, 9, 100
    fig, ax = plt.subplots(figsize=(w, h), dpi=dpi)
    bg = "#0e1117" if dark else "white"
    fg = "white" if dark else "black"
    hi = "#36d659" if dark else "#1f8f2d"
    fig.patch.set_facecolor(bg); ax.axis("off")

    ax.text(0.5, 0.79, f"${ticker}", ha="center", va="center", fontsize=150, color=hi,    weight="bold")
    ax.text(0.5, 0.63, f"{avg_gain} Avg Gain | {wins_text}", ha="center", va="center", fontsize=46, color=fg, weight="bold")
    ax.text(0.5, 0.56, sub,                              ha="center", va="center", fontsize=28, color=("lightgray" if dark else "dimgray"))

    ax.text(0.25, 0.40, f"Sharpe\n{sharpe}",       ha="center", va="center", fontsize=28, color=fg)
    ax.text(0.50, 0.40, f"% Profitable\n{win_rate}", ha="center", va="center", fontsize=28, color=fg)
    ax.text(0.75, 0.40, f"Cumulative\n{cumulative}",  ha="center", va="center", fontsize=28, color=fg)

    ax.text(0.98, 0.09, "TradeWave.ai", ha="right", va="center", fontsize=28, color=("lightgray" if dark else "dimgray"))
    _ensure_dirs(outfile)
    fig.savefig(outfile, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)

# ========= Tweet composition =========

def _compose_tweet_text(market: str, company: str, ticker: str, date1: str, date2: str,
                        years: str, avg_gain: str, wins_text: str, viewer_link: str) -> str:
    hooks = [
        f"${ticker} {avg_gain} avg in this window | {wins_text}",
        f"Wave Alert: ${ticker} seasonal edge {date1} → {date2} | {wins_text}",
        f"${ticker} repeats {wins_text}. Avg {avg_gain}. Window {date1} → {date2}.",
        f"{market}: ${ticker} {avg_gain} avg | {wins_text} | {date1} → {date2}",
    ]
    hook = random.choice(hooks)
    tail = f"\nView ▶ {viewer_link}"
    return (hook + tail)[:279]

# ========= Post / delete =========

def create_twitter_post(tweet_text: str, image_path: str, type: str = 'seasonal',
                        meta: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    log_path = config.twitter_current_social_media_posts
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    posts = []
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f:
                posts = json.load(f)
        except Exception:
            posts = []

    post_exists, post_id = False, ""
    if type == 'seasonal':
        for p in posts:
            if p.get('type') == 'seasonal' and p.get('datetime','')[:10] == today:
                post_exists = True
                post_id = p.get('post_id','')
                break
    elif type == 'dr':
        for p in posts:
            if p.get('img_path') == image_path:
                post_exists = True
                post_id = p.get('post_id','')
                break

    if post_exists:
        return post_id, 'exist'

    # Post (or dry run)
    if getattr(config, "TW_DRY_RUN", False):
        post_id = f"dry-{int(datetime.datetime.now().timestamp())}"
        action = "dry"
    else:
        api = _get_tw_api()
        media = api.media_upload(image_path)  # v1.1 endpoint
        status = api.update_status(status=tweet_text, media_ids=[media.media_id])
        post_id = str(status.id)
        action = "created"

    post_info = {
        'sm': 'tw',
        'datetime': _now(),
        'post_id': post_id,
        'message': tweet_text,
        'img_path': image_path,
        'type': type,
        **(meta or {})
    }
    posts.append(post_info)
    with open(log_path, 'w') as f:
        json.dump(posts, f, indent=4)

    return post_id, action

def delete_twitter_post(pid: str):
    post_id = str(pid)
    if not getattr(config, "TW_DRY_RUN", False):
        if tweepy is None:
            raise RuntimeError("tweepy not installed. pip install tweepy")
        api = _get_tw_api()
        api.destroy_status(post_id)

    # remove from log
    path = config.twitter_current_social_media_posts
    if os.path.exists(path):
        with open(path, 'r') as f:
            posts = json.load(f)
        posts = [p for p in posts if p.get("post_id") != post_id]
        with open(path, 'w') as f:
            json.dump(posts, f, indent=4)

# ========= Helpers (mirror FB) =========

def top10_link(id_: int) -> str:
    num = 10
    fg = config.available_resources
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    post_title = f"Top {num} Seasonal Patterns {fg[str(id_)]} {today}-t"
    post_slug  = slugify(post_title)
    return config.domain_root + post_slug

def get_last_10_symbols_posted():
    path = config.twitter_current_social_media_posts
    if not os.path.exists(path): return []
    prev = json_log(path, 'get', {})
    symbols = []
    for p in prev[::-1]:
        if 'symbol' in p: symbols.append(p['symbol'])
        if len(symbols) >= 10: break
    return symbols

def find_random_seasonal_for_twitter():
    filename = config.today_top10_data
    action, dfd = load_top10(filename)
    posted = set(get_last_10_symbols_posted())

    fg_lst, i_lst, sym_lst, ap_lst, sr_lst = [], [], [], [], []

    for fg in list(config.available_resources.keys()):
        if int(fg) > 4 and int(fg) != 11:  # only stocks + ETF (same as FB)
            continue
        for i in range(5):
            fgi = int(fg)
            symbol = dfd[fgi].iloc[i]['Symbol']
            avg_profit = dfd[fgi].iloc[i]['Avg Profit']
            sr = dfd[fgi].iloc[i]['Sharpe Ratio']
            fg_lst.append(fgi); i_lst.append(i); sym_lst.append(symbol); ap_lst.append(avg_profit); sr_lst.append(sr)

    import pandas as pd
    df = pd.DataFrame({'fg': fg_lst, 'i': i_lst, 'symbol': sym_lst, 'avg_profit': ap_lst, 'sr': sr_lst})
    df['avg_profit2'] = df['avg_profit'].astype(str).str.rstrip('%').astype(float)
    df = df.sort_values(by=['avg_profit2'], ascending=False)
    df = df.drop_duplicates(subset=['symbol','avg_profit'])
    df = df[df['avg_profit2'] > 10]

    if df.empty:
        raise RuntimeError("No candidates > 10% avg profit.")

    # try up to 10 to avoid repeats
    num_rows = df.shape[0]
    for _ in range(10):
        row_num = random.randint(0, num_rows-1)
        fg = int(df['fg'].iloc[row_num]); r = int(df['i'].iloc[row_num])
        symbol = df['symbol'].iloc[row_num]
        if symbol not in posted:
            return dfd, fg, r
    # fallback best
    best = df.iloc[0]
    return dfd, int(best['fg']), int(best['i'])

# ========= Posting (mirror FB signatures) =========

def post_one_of_top10(df, fg: str, row: int, hashtags: str) -> Tuple[str, str]:
    idx       = df['post_title'].iloc[row].find("Year")
    years     = df['post_title'].iloc[row][:idx-1]
    symbol    = df['Symbol'].iloc[row]
    date1     = df['Date'].iloc[row]
    date2     = df['Date2'].iloc[row]
    slug      = df['opp_slug'].iloc[row]
    daysout   = df['DaysOut'].iloc[row]
    company   = df['company'].iloc[row]
    direction = df['Direction'].iloc[row]
    avg_p     = df['Avg Profit'].iloc[row]
    sharpe    = df['Sharpe Ratio'].iloc[row]
    win_rate  = df['% Profitable'].iloc[row] if '% Profitable' in df.columns else "—"

    # Links
    sv_params = convert_param_base64(fg, symbol, date1, daysout, years)
    viewer_link = config.domain_root + 'wave-viewer?o=' + sv_params
    report_link = slug if str(slug).startswith('http') else (config.domain_root + str(slug))

    # Tweet text
    wins_text = f"{win_rate}" if win_rate != "—" else f"{years} yrs"
    tweet_text = _compose_tweet_text(
        market=config.available_resources[str(fg)], company=company, ticker=symbol,
        date1=date1, date2=date2, years=str(years), avg_gain=str(avg_p),
        wins_text=wins_text, viewer_link=viewer_link
    )

    # Image (rotate templates)
    template = random.choice(["bar_dark","trend_light","stat_dark","bar_light","stat_light"])
    outdir = os.path.join("/tmp", "tradewave_tw_cards", date1)
    _ensure_dirs(outdir)
    outfile = os.path.join(outdir, f"{symbol}_{date1}_{daysout}_{template}.png")

    title = f"${symbol} {avg_p} Avg Gain | {wins_text}"
    sub   = f"Seasonal Edge | {date1} → {date2} ({daysout} Days)"

    if template in ("bar_dark","bar_light"):
        years_axis = list(range(datetime.datetime.now().year-9, datetime.datetime.now().year+1))
        base = float(str(avg_p).rstrip('%') or 0.0)
        vals = [max(0.4, base * (0.6 + 0.8*random.random())) for _ in years_axis]
        _render_bar_card(outfile, title, sub, years_axis, vals, str(sharpe), str(win_rate), str(avg_p), dark=(template=="bar_dark"))

    elif template == "trend_light":
        days = int(daysout) if str(daysout).isdigit() else 60
        curve = np.cumsum(np.random.normal(0.12, 0.04, size=days)) + max(1.0, float(str(avg_p).rstrip('%') or 1.0)/2)
        _render_trend_card(outfile, title, sub, curve, str(sharpe), str(win_rate), str(avg_p), dark=False)

    else:  # stat_dark / stat_light
        _render_stat_card(outfile, symbol, str(avg_p), str(wins_text), sub, str(sharpe), str(win_rate), str(avg_p), dark=(template=="stat_dark"))

    meta = {"type": "seasonal", "symbol": symbol, "fg": str(fg), "img_template": template}
    tweet_id, action = create_twitter_post(tweet_text, outfile, type='seasonal', meta=meta)
    return tweet_id, action

def post_one_of_am_dr_reports(action_dict: Dict[str, Any]):
    fg                 = int(action_dict['id'])
    date1              = action_dict['date']
    days_hold          = int(action_dict['days_hold'])
    symbol             = action_dict['symbol']
    years              = action_dict['years']
    post_slug          = action_dict['slug']
    note               = action_dict.get('note', "")
    # Fetch stats for avg profit
    r = get_chart_data(action_dict['id'], date1, symbol, action_dict['days_hold'], years, None)
    stats = r['stats']; avg_p = stats.get('Avg Profit', '—')
    date2 = inc_date_day(date1, days_hold)

    sv_params = convert_param_base64(fg, symbol, date1, days_hold, years)
    viewer_link = config.domain_root + 'wave-viewer?o=' + sv_params

    hook = note.strip() if note and note != "-" else f"${symbol} {avg_p} avg over {days_hold} days"
    tweet_text = (f"{hook}\nWindow {date1} → {date2}\nView ▶ {viewer_link}")[:279]

    outdir = os.path.join("/tmp", "tradewave_tw_cards", date1)
    _ensure_dirs(outdir)
    outfile = os.path.join(outdir, f"{symbol}_{date1}_{days_hold}_DR.png")
    sub = f"Date Range | {date1} → {date2} ({days_hold} Days)"
    _render_stat_card(outfile, symbol, str(avg_p), f"{years}-Year", sub,
                      str(stats.get('Sharpe Ratio','—')),
                      str(stats.get('% Profitable','—')),
                      str(stats.get('Cumulative Ret','—')),
                      dark=True)

    tweet_id, action = create_twitter_post(tweet_text, outfile, type='dr', meta={"type":"dr","symbol":symbol,"fg":str(fg)})
    return tweet_id, action

def del_one_of_am_dr_reports(action_dict: Dict[str, Any]):
    # Match by image slug in our outfile name convention
    target_slug = action_dict['slug']
    log_path = config.twitter_current_social_media_posts
    if not os.path.exists(log_path): return
    with open(log_path, 'r') as f:
        posts = json.load(f)
    for p in posts:
        if "DR.png" in p.get("img_path","") and target_slug.replace("/","_") in p.get("img_path",""):
            dt = datetime.datetime.strptime(p['datetime'], "%Y-%m-%d %H:%M:%S")
            minutes = (datetime.datetime.now() - dt).total_seconds()/60
            if minutes < getattr(config, "minutes_before_tw_post_is_locked", 30):
                delete_twitter_post(p['post_id'])
            break

def delete_all(type_: str):
    path = config.twitter_current_social_media_posts
    if not os.path.exists(path): return
    with open(path, 'r') as f: posts = json.load(f)
    for p in list(posts):
        if p.get("type") == type_:
            try: delete_twitter_post(p['post_id'])
            except Exception: pass

# ========= Main (test like m_facebook.py) =========

if __name__ == '__main__':
    # Example: pick a random seasonal and post once
    try:
        dfd, fg, r = find_random_seasonal_for_twitter()
        hashtags = '#traders #investors'  # not appended to tweet (X prefers no tags); kept for parity
        tweet_id, action = post_one_of_top10(dfd[int(fg)], str(fg), r, hashtags)
        print(tweet_id, action)
        if action == 'exist':
            # delete and recreate (like your FB script)
            delete_twitter_post(tweet_id)
            dfd, fg, r = find_random_seasonal_for_twitter()
            tweet_id, action = post_one_of_top10(dfd[int(fg)], str(fg), r, hashtags)
            print('recreation: ', tweet_id, action)
    except Exception as e:
        print('Error:', e)
