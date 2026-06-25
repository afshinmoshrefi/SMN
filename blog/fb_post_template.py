# this is templates for the text displayed on facebook posts
# there are 3 parts
# 1) begining title
#       Wave Alert!  Wave Spotlight!  Wave Forecast!  Wave Update!  Wave Watch!
# 2) Text1 - 11 variations
# 3) Text2 - 1 version of text - no variation

import random
import os

market,company,ticker,date1,date2,years,prob_years,avg_gain,days,report_link,open_in_wave_viewer_link,top10_link='','','','','','','','','','','','',
#----------------------------------------------------------------------------------------------------------------------------------
title_text = [
    '🌊 Wave Alert!',
    '🌊 Wave Spotlight!',
    '🌊 Wave Forecast!',
    '🌊 Wave Update!',
    '🌊 Wave Watch!'
]



#----------------------------------------------------------------------------------------------------------------------------------
text1_p = [
    """ Dive into today's standout pattern in the {market}, where {company} ({ticker}) is poised to make a splash. Plan to buy on {date1} and ride the wave until {date2}.

Impressively, in {years} of the last {prob_years} years, it has yielded an average gain of {avg_gain} within a consistent {days} day window in the same date range.  This is one wave you won't want to miss! 🏄
    """,


    """ In the vast expanse of the {market}, {company} ({ticker}) is making waves. Get ready to buy on {date1} and ride the momentum until {date2}.  

This pattern has consistently shown gains for {years} out of the past {prob_years}-years, with an average gain of {avg_gain} over a {days} day window.  Wave hello to a winning portfolio when you add this to it! 🏄‍♂️
    """,



    """ The waves in the {market} are rolling in, and {company} ({ticker}) has caught our full attention. 

Its trajectory from {date1} to {date2} is noteworthy for being profitable in {years} out of the last {prob_years} years, with an average profit of {avg_gain} over the same {days} days each year.  Whatcha wave-ing for?  Grab your board and let’s go! 🏄‍♂️
    """,


    
    """ Among the ebbs and flows of the {market}, the wave that stands out most right now is {company} ({ticker}).

Over a span of {days} days year after year, from {date1s} to {date2s}, it has repeatedly made a profit in {years} out of the past {prob_years} years, with an average gain of {avg_gain}.  Are you ready to go with the flow? 🏄‍♂️
    """,


    
    """ Amidst a vast ocean of opportunities in the {market}, you can see this wave cresting from a mile away:  {company} ({ticker}).

In {years} of the past {prob_years} years, over the same {days} day date range from {date1s} to {date2s}, it has averaged a solid {avg_gain} return.  What’s stopping it from repeating that very predictable pattern again this year? 🏄‍♂️
    """,


    
    """ The tide is turning in {market}.  With currents shifting and the horizon in view, {company} ({ticker}) has emerged as a very promising trend.

From {date1s} to {date2s}, year after year after year for {years} out of the last {prob_years} years, it has averaged a generous {avg_gain} gain over those {days} days.  Will it continue to build momentum? 🏄‍♂️
    """,


    
    """ The waters of the {market} are stirring, and one wave is rising higher than all the rest:  {company} ({ticker}).

As it travels over the next {days} days, from {date1s} to {date2s}, will it achieve the same {avg_gain} return that it has averaged for {years} years out of the last {prob_years} years?  There’s only one way to find out…  🏄‍♂️
    """,


    
    """  As the currents of the {market} shift, {company} ({ticker}) is emerging as a tidal force.  Set your sights on {date1s} and surf this trend until {date2s}.

Because over the past {years} out of {prob_years} years, during the {days} days in that date range, it has averaged a respectable {avg_gain} return.  You don’t find that kind of predictability very often…  unless you’re surfing with TradeWave, that is!  🏄‍♂️
    """,


    
    """  While navigating the deep waters of the {market}, {company} ({ticker}) is the beacon we’ve been waiting for.  Mark {date1} on your calendar and sail smoothly until {date2}.

If an annual {avg_gain} average return sounds good to you, it’ll sound even better once you find out that’s been the average over the same {days} days for the past {years} years out of {prob_years} years!  Why sail anywhere else?  🏄‍♂️
    """,


    
    """ In the ever-changing seascape of the {market}, the wave every surfer dreams of is {company} ({ticker}).  Catch it on {date1} and ride it all the way to {date2}.

Over those {days} days, it has averaged an otherworldly return of {avg_gain} for the past {years} out of {prob_years} years.  That’s the kind of dream you don’t want to wake up from!  🏄‍♂️
    """,


    
    """  The {market} is filled with hidden depths, but {company} ({ticker}) is the treasure every diver hopes for.  Start your expedition on {date1} and claim your bounty by {date2}.

For the last {years} out of {prob_years} years, treasure hunters have repeatedly struck gold over the {days} days that span those two dates, with an average profit of {avg_gain}.  Anchors aweigh!  ⚓
    """,


    




]
#----------------------------------------------------------------------------------------------------------------------------------
text2_p = """\nDive deeper with our full report at {report_link} to unlock key market insights. 

View this opportunity (and more like it) on our live Wave Viewer software at {open_in_wave_viewer_link}.

Check out all 10 of today’s Top 10 Wave Trades at {top10_link}.

#TradingTips #TradingStrategy #SwingTrading #TechnicalAnalysis #TradeTheWave

Note:  This is not investment advice.  Past performance does not equal future results.  Consult your financial advisor before taking any trades."""

#----------------------------------------------------------------------------------------------------------------------------------
# 
#----------------------------------------------------------------------------------------------------------------------------------

def get_random_facebook_content(market,company,ticker,date1,date2,years,prob_years,avg_gain,days,report_link,open_in_wave_viewer_link,top10_link):
    
    savedSequenceFile = 'seq'  # save sequence of the paragraphs. reset after the last to 0

    
    date1s=date1[5:]
    date2s=date2[5:]

    fb_post_values = {
        'market':market,
        'company':company,
        'ticker':ticker,
        'date1':date1,
        'date2':date2,
        'date1s':date1s, # these are the short versions of date without the year 
        'date2s':date2s,
        'years':years,
        'prob_years':prob_years,
        'avg_gain':avg_gain,
        'days':days,
        'report_link':report_link,
        'open_in_wave_viewer_link':open_in_wave_viewer_link,
        'top10_link':top10_link
    }


    title = random.choice(title_text)

    # get paragraph index sequentially - save it in a file
    # this way everytime we use the next paragraph in our list of 11 initially
    # when the last one is used, go back to 0
    paragraph_index = 0 # index of which of
    if os.path.exists(savedSequenceFile):
        with open(savedSequenceFile,'r') as file:
            paragraph_index = int(file.readline())
        paragraph_index +=1 # increment
        if paragraph_index > len(text1_p) -1:
            paragraph_index = 0 
    with open(savedSequenceFile,'w') as file:
        file.write(str(paragraph_index))
    

    # use the incremeted sequence
    p1 = text1_p[paragraph_index].format(**fb_post_values)
    p2 = text2_p.format(**fb_post_values)


    return title,p1,p2


#----------------------------------------------------------------------------------------------------------------------------------

# title,p1,p2 = get_random_facebook_content('Dow 30 Stocks','Microsoft','MSFT','2023-09-12','2023-11-22',10,10,'12.4%',44,'','','')

# print(p1)


