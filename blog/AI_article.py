
# AI Article, take a tradewave opportunity, creates a prompt for a article, then send the prompt to an LLM - 
# 3 types of LLM are imported


from article_images import create_article_images
from create_report  import get_keyprovider_token, login_appserver, get_chart_data
from article_prompt import create_article_prompt
from AI_tools       import send_openai_prompt, send_perplexity_prompt, send_grok_prompt
from blog_tools     import get_company_name

from article_prompt import _PERP_SYSTEM # you are a financial journalist ... 


# strips the <think> </think> text from the result - it happens with perplexity API result
def strip_think_block(text: str) -> str:
    end_tag = "</think>"
    pos = text.find(end_tag)
    if pos != -1:
        return text[pos + len(end_tag):].lstrip()
    return text


#---------------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':

    print('TradeWave AI Seasonal Article Generator')

    image_size_key = 'x'

    # '0': 'DOW 30 STOCKS',
    # '1': 'NASDAQ 100 STOCKS',
    # '2': 'S&P 500 STOCKS',
    # '3': 'RUSSELL 1000 STOCKS',
    # '4': 'WILSHIRE 5000 STOCKS',
    # '5': 'INDICES COMMON',
    # '6': 'INDICES',
    # '7': 'FUTURES & COMMODITIES',
    # '8': 'FOREX ALL',
    # '9': 'FOREX LIQUID',
    # '10': 'GOVERNMENT BONDS',
    # '11': 'ETF',

    # the opportunity
    resource_id=2
    date="2025-09-19"
    symbol="MSI"
    days="72"
    years="14"
    #-------------------------
    theme="light"
    price_lookback_days = 300 # used for generating a recent price chart of the security
     # Choose a scenario by setting only the variant; market family is auto-detected
    variant = 1
    byline = "Powered by TradeWave.AI"
    ai_disclosure = False


    # create article images 
    img_paths = create_article_images(image_size_key,resource_id,date,symbol,days,years,theme)
    # get opportunity stats fro chartData4
    keyprovider_token = get_keyprovider_token()
    appserver_token = login_appserver(keyprovider_token)
    cdata = get_chart_data(resource_id, date, symbol, days, years, True, appserver_token)
    # Company/instrument name lookup
    try:
        company = get_company_name(resource_id, symbol) or symbol
    except Exception:
        company = symbol
    # create prompt
    variant_index = 1 # changes template
    article_prompt = create_article_prompt(cdata,img_paths,company,resource_id,variant_index,byline,ai_disclosure)    
    # send prompt to LLM
    # print(article_prompt)
    file_name = "prompt.txt"
    with open(file_name, "w") as file:
        file.write(article_prompt)

    # openai_reply = send_openai_prompt(
    #     article_prompt,
    #     model="gpt-4.1",                # "gpt-5" API doesn't have web search nov 2025 - 
    #     system=_PERP_SYSTEM,
    #     temperature=0.2,
    #     stream=False,
    #     max_tokens=6000,
    #     tools=[{"type": "web_search"}],
    #     tool_choice="auto",
    # )

    # file_name = "article.html"
    # with open(file_name, "w") as file:
    #     file.write(openai_reply)

    # print(openai_reply)

    perplexity_reply = send_perplexity_prompt(
        article_prompt,
        model="sonar-pro", # do not use "sonar-reasoning-pro" according to chatGPT only use sonar-pro
        system=_PERP_SYSTEM,
        temperature=0.2,
        stream=False,
        search_mode="web",
        enable_search_classifier=True,
        search_recency_filter="month",
        web_search_options={"search_context_size": "high"},
        max_tokens=6000,
    )
    perplexity_reply = strip_think_block(perplexity_reply) # strip <think> block
    file_name = "articlep.html"
    with open(file_name, "w") as file:
        file.write(perplexity_reply)
    print(perplexity_reply)