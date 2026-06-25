#!/usr/bin/env python3
 
import json
import time
import traceback
import datetime
import os
import redis

import sys
sys.path.insert(0, "/home/flask")

from article_workflow import generate_news_article
import config

LOG_DIR = "/home/flask/blog/logs"
LOG_PATH = os.path.join(LOG_DIR, "news_runs.jsonl")

NEWS_QUEUE_NAME = config.NEWS_QUEUE_NAME

# Same Redis settings as blog_queue / blog_processor
redis_client3 = redis.Redis(host="localhost", port=6379, db=config.articles_redis_db)

def log_article_run(tracking: dict) -> None:
    """
    Append a compact JSON row describing this run to LOG_PATH.
    Side-band only: if this fails, the worker still continues.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        row = {
            "ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "resource_id": tracking.get("resource_id"),
            "symbol": tracking.get("symbol"),
            "start_date": tracking.get("start_date"),
            "days": tracking.get("days"),
            "years": tracking.get("years"),
            "status": tracking.get("status"),
            "error_step": tracking.get("error_step"),
            "error_message": tracking.get("error_message"),
            "duration_seconds": tracking.get("duration_seconds"),
        }

        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        print(f"[TRACK] Logged article run → {LOG_PATH}")
    except Exception as e:
        print(f"[WARN] Failed to save tracking: {e}")

def process_message(action_dict: dict) -> None:
    """
    Handle a single 'write_news_article' job.

    Expected payload fields (all strings):
      - action         : "write_news_article"
      - resource_id
      - symbol
      - date          : pattern_start_date (YYYY-MM-DD)
      - days          : pattern_window_days
      - years         : MUST remain a string
      - userid
      - article_publish_date  : YYYY-MM-DD, can be in the future
      - mode          : should be "2" for full async article generation
    """
    action = action_dict.get("action")
    if action != "write_news_article":
        print(f"[WARN] Unknown action '{action}', ignoring message: {action_dict}")
        return

    resource_id          = str(action_dict.get("resource_id", ""))
    symbol               = str(action_dict.get("symbol", ""))
    date                 = str(action_dict.get("date", ""))
    days                 = str(action_dict.get("days", ""))
    years                = str(action_dict.get("years", ""))  # KEEP AS STRING
    direction            = str(action_dict.get("direction","")) # long or short
    userid               = str(action_dict.get("userid", ""))
    article_publish_date = str(action_dict.get("article_publish_date", ""))
    mode                 = str(action_dict.get("mode", "2"))  # default to "2"
    pattern_mode         = str(action_dict.get("pattern_mode", "consecutive") or "consecutive")  # 'pe' or 'consecutive'
    note                 = str(action_dict.get("note", ""))

    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    # process future publish dates - park in a separate holding key so they don't block the main queue
    if article_publish_date and article_publish_date > today_date:
        holding_key = f"{NEWS_QUEUE_NAME}_future"
        print(f"[INFO] article_publish_date={article_publish_date} is in the future. Parking in '{holding_key}'.")
        redis_client3.rpush(holding_key, json.dumps(action_dict))
        return


    print("\n[INFO] Processing write_news_article job:")
    print(f"       resource_id={resource_id}, symbol={symbol}, date={date}, "
          f"days={days}, years={years}, userid={userid}, article_publish_date={article_publish_date}, "
          f"mode={mode}, pattern_mode={pattern_mode}")

    try:
        result = generate_news_article(
            resource_id=resource_id,
            symbol=symbol,
            date=date,
            days=days,
            years=years,
            direction=direction,
            userid=userid,
            article_publish_date=article_publish_date,
            mode=mode,
            pattern_mode=pattern_mode,
            note=note,
        )

        # Persist tracking for debugging / analytics
        if isinstance(result, dict):
            log_article_run(result)

        # Make a safe copy and remove huge fields like HTML
        safe_result = dict(result) if isinstance(result, dict) else {"raw": str(result)}
        safe_result.pop("article_html", None)

        print("[INFO] generate_news_article summary:")
        print(
            json.dumps(
                {
                    "status": safe_result.get("status"),
                    "error_message": safe_result.get("error_message"),
                    "publish_url": safe_result.get("publish_result", {}).get("url")
                        if isinstance(safe_result.get("publish_result"), dict)
                        else None,
                    "duration_seconds": safe_result.get("duration_seconds"),
                },
                indent=2,
            )
        )

    except Exception as e:
        print("[ERROR] Exception while processing write_news_article job:")
        print(e)
        traceback.print_exc()


def main() -> None:
    print(f"[START] article_processor listening on Redis list '{NEWS_QUEUE_NAME}'")

    holding_key = f"{NEWS_QUEUE_NAME}_future"

    while True:
        try:
            # Before blocking, check if any future-dated articles are now due
            today_date = datetime.datetime.now().strftime("%Y-%m-%d")
            held = redis_client3.llen(holding_key)
            if held:
                promoted = 0
                for _ in range(held):
                    raw = redis_client3.lpop(holding_key)
                    if raw is None:
                        break
                    msg = json.loads(raw.decode("utf-8"))
                    if msg.get("article_publish_date", "") <= today_date:
                        redis_client3.rpush(NEWS_QUEUE_NAME, json.dumps(msg))
                        promoted += 1
                        print(f"[INFO] Promoted future article {msg.get('symbol')} (pub={msg.get('article_publish_date')}) to main queue.")
                    else:
                        redis_client3.rpush(holding_key, raw)  # put back, still not due
                if promoted:
                    print(f"[INFO] Promoted {promoted} future article(s) to main queue.")

            # Blocks until a message is available (timeout 60s so we re-check future queue periodically)
            result = redis_client3.blpop(NEWS_QUEUE_NAME, timeout=60)
            if result is None:
                continue  # timeout, loop back to check future queue
            key, message = result

            # message is bytes → decode for safety
            try:
                action_dict = json.loads(message.decode("utf-8"))
            except Exception as e:
                print("[ERROR] Failed to decode JSON message from queue:")
                print(e)
                print("Raw message:", message)
                continue

            print(f"\n[DEBUG] Raw message from {key.decode('utf-8')}: {action_dict}")

            process_message(action_dict)

            # Optional: show remaining queue length
            q_len = redis_client3.llen(NEWS_QUEUE_NAME)
            print(f"[INFO] Queue '{NEWS_QUEUE_NAME}' length now: {q_len}")

        except Exception as outer_e:
            # Catch-all so the worker never dies completely
            print("[FATAL] Unhandled exception in main loop, sleeping 5 seconds...")
            print(outer_e)
            traceback.print_exc()
            time.sleep(5)

if __name__ == "__main__":
    
    main()
