
import requests
import pandas as pd
import os
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
from blog_tools import wait_for_php, search_posts_by_title
from slugify import slugify

from generate_opp_blogs import run_opp_blog_generation



sys.path.insert(0, '/home/flask')
import config




#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    # run_opp_blog_generation(today_date)

    financial_groups = config.available_resources



    html_dict=run_opp_blog_generation(today_date,True)

    print('\n\n\n\n\n ----------------------------------------------------------',html_dict)