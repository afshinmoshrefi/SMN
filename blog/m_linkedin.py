# changed the parameters of 
# run_opp_blog_generation(opp_date):
# added parameter html_table_only boolean
# this is used to only create an html table for each opportunities list

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

    
#------------------------------------------------------------------------------------------------
#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    filename = config.today_top10_data


    action,dfd=load_top10 (filename)

    print(action)
    print(dfd[4].columns)





