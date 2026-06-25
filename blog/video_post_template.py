# this script create random scripts for videos - there are 8 segments for each video as follows:

# 1) - opening text - display the thumbnail or display the text ?
# 1d)- disclaimer
# 2) - trade detail text - display table 1
# 3) - barchart text - display the barchart
# 4) - gainloss1 text - display table 2
# 5) - info text - display table 3
# 6) - cumulative text - display cumulative chart
# 7) - trend chart - display trend chart
# 8) - closing text + CTA - display text & CTA - include 9)

# there are multiple versions of each of the 8

# t9 is merged to t8 inside the code - final clip merges 8 smaller clips - last clip audio is t8+t9

# when script for a new video is generated, there are random text for each of the 8 segments are selected.  
# variations for each part may be different but eh final result is to return 8 scripts that will be narrated

t1 = [

    '''The wave report for {company}, with the ticker symbol {symbol}, shows the historical performance between {daterange}. <break time="600ms"/> For the past {years} years, the average annual gain for that {daysOut} day period has been {avg_profit}. <break time="600ms"/> Let’s take a closer look!  <break time="600ms"/> While you watch, click the link in the description to see this in our interactive Wave Viewer.''',

    '''Join us as we dive into the wave report for {company}, with ticker {symbol}.  <break time="600ms"/> Between {daterange}, the currents are carrying untapped rewards.  Over the last {years} years, this {daysOut} day wave averaged an annual  gain of {avg_profit}. <break time="600ms"/> Let’s surf the details! <break time="600ms"/> While you watch, hit the link in the description to sail along with us in the interactive Wave Viewer.''',

    '''The wave report signals high tide for {company}, also known as {symbol}. <break time="600ms"/> Your ideal timeframe stretches from {daterange}. Historically, over the last {years} years, the annual gains for this {daysOut} day period average {avg_profit}.<break time="600ms"/> Let's dive deeper!  <break time="600ms"/> Are you ready to take the plunge? <break time="600ms"/> The Wave Viewer link in the description will lead you to an interactive journey through this wave report.''',

    '''Let's chart the waters with our wave report for {company}, ticker {symbol}. <break time="600ms"/> Between {daterange}, this {daysOut} day period shows an average annual gain of {avg_profit} over the last {years} years. <break time="600ms"/>Get ready to leave the harbor!  <break time="600ms"/> Sail along with us by hitting the link in the description.  That will bring up this trade on our interactive Wave Viewer.''',

    '''Let’s surf into the wave report for {company}.  That's the ticker symbol {symbol}. The current is strong between {daterange}, showing an average annual gain of {avg_profit} over the last {years} years for this {daysOut} day wave. <break time="600ms"/> Get ready to catch this wave?  <break time="600ms"/> Keep the stoke going by clicking the link in the description. It'll take you to our Wave Viewer for a fully interactive experience.''',

    '''Cast your net on our wave report for {company}, ticker {symbol}. <break time="600ms"/> From {daterange}, this {daysOut} day tide has brought annual gains averaging {avg_profit}. At least according to the last {years} years. <break time="600ms"/> Let's reel this in!  <break time="600ms"/> Click the link in the description to take the full voyage with our interactive Wave Viewer as you watch and listen.''',

    '''Surf's up! Our wave report for {company}, ticker {symbol}, points to a crest between {daterange}. Over each of the past {years} years, this {daysOut} day ride has scored an average gain of {avg_profit}.<break time="600ms"/> Let's hit the water!  <break time="600ms"/> To catch this wave in all its glory, hit the link below to pull it up on our interactive Wave Viewer.''',

    '''Hoist the sails! The wave report for {company}, ticker symbol {symbol}, indicates smooth sailing from {daterange}. Historically, over each of the past {years} years, this {daysOut} day span has delivered an average gain of {avg_profit}.<break time="600ms"/> Chart this voyage interactively by hitting the link in the description, steering you directly to this trade on our Wave Viewer.''',

    '''The wave report for {company}, ticker {symbol}, shows smooth waters between {daterange}. <break time="600ms"/>On average, this {daysOut} day cruise has yielded a gain of {avg_profit} over each of the past {years} years.  <break time="600ms"/> Embark on the full journey by hitting the link in the description. Our Wave Viewer will serve as your interactive travel guide.''',

    '''Let this wave report be your north star for {company}, ticker {symbol}. <break time="600ms"/> For {years} years, the favorable winds have been blowing between {daterange}, with annual gains averaging {avg_profit}. <break time="600ms"/> Click the link in the description for a celestial view in our interactive Wave Viewer.''',
]


t1_disclaimer = [
    '''<break time="700ms"/>Please note <break time="600ms"/> The following is based on a {years} year history.  This is not investment advice.  Past performance does not ensure future results.  Consult a financial advisor before taking any trades.'''
]


t2_long = [
    '''{company}, part of the {security_group_name}, is a {direction} wave possibility if you were to buy {symbol} on {dr1}, hold for {daysOut} days, and sell on {dr2}. This analysis relies on {years} years of historical data.''',

    '''This is a {direction} wave possibility for {symbol}. <break time="600ms"/> Get in on {dr1}, exit on {dr2}. All based on a {years}-year track record for {company}, which is part of {security_group_name}.''',

    ''' Within the {security_group_name}, we’ve discovered that {company} is ready to go {direction} if you were to buy {symbol} on {dr1}, keep it for {daysOut} days, and let it go on {dr2}. This comes from a {years}-year historical analysis.''',

    '''With {years} years of history, this {direction} wave possibility for {company}, part of {security_group_name}, is promising if you were to buy {symbol} on {dr1}, sit tight for {daysOut} days, and sell on {dr2}.''',

    ''' For {company}, which is part of {security_group_name}, the data strongly suggests a {direction} wave possibility. <break time="600ms"/> It involves buying {symbol} on {dr1}, holding for {daysOut} days, and selling on {dr2}. This is supported by {years} years of data.''',
]
   
t2_short = [
    ''' For {company} in {security_group_name}, the strategy suggests selling {symbol} on {dr1}, waiting {daysOut} calendar days, and buying it back on {dr2}. This is based on {years} years of data.''',

    '''For {company} (ticker symbol {symbol}), a member of {security_group_name}, consider selling on {dr1}, waiting {daysOut} days, and repurchasing on {dr2}. This strategy is supported by {years} years of historical data.''',

    '''The report for {company}, belonging to {security_group_name}, proposes selling {symbol} on {dr1}, waiting {daysOut} calendar days, and buying back on {dr2}. This is coming from {years} years of historical data.''',

    ''' With {years} years of history, the report for {company}, part of the {security_group_name}, indicates selling {symbol} on {dr1}, waiting for {daysOut} days, and buying it back on {dr2}.''',

    '''The {years}-year historical report for {company}, a member of the {security_group_name}, points to selling {symbol} on {dr1}, followed by a {daysOut} day wait, and then buying on {dr2}.''',
]


t3_long =[
    
    '''The gain-loss bar chart for this wave shows the percentage gains of {symbol} when bought on {dr1} and sold on {dr2} for each of the past {years} years.  Click on the bar for any year on the wave viewer to see the price chart for that year’s date range.''',

    '''View the gain-loss bar chart to see the percentage gains of {symbol} for each of the past {years} years when bought on {dr1} and sold on {dr2}. Click a bar on the wave viewer and you’ll see the price chart for the corresponding year.''',

    '''Check out the gain-loss bar chart showing the percentage gains for {symbol} over each of the past {years} years when bought on {dr1} and sold on {dr2}. In the Wave Viewer, click on any bar to display that year's price chart.''',

    '''The gain-loss bar chart reveals how {symbol} performed during the previous {years} years when acquired on {dr1} and sold on {dr2}. To view the yearly price chart for a specific year, click the corresponding bar in the Wave Viewer.''',

    '''Take a look at the gain-loss bar chart to see yearly percentage gains for {symbol} bought on {dr1} and sold on {dr2}, year after year, for the past {years} years. Clicking on any bar in the Wave Viewer will reveal the price chart for that year.''',
]


t3_short =[

    '''The gain-loss bar chart reveals the percentage gains for {symbol} when sold on {dr1} and bought back on {dr2}, spanning the past {years} years.  Click on the bar for any year on the wave viewer to see the price chart for that year’s date range.''',

    '''View the profit bar chart to see the percentage gains of {symbol} when sold on {dr1} and bought on {dr2} over the past {years} years. Click a bar on the wave viewer and you’ll see the price chart for the corresponding year.''',

    '''In the gain-loss bar chart, you can see the gains for each of the past {years} years when {symbol} was sold on {dr1} and purchased on {dr2}.  In the Wave Viewer, click on any bar to display that year's price chart.''',

    '''See the percentage gains for {symbol} in the gain-loss bar chart, covering sell dates on {dr1} and buy dates on {dr2} for the past {years} years.  To view the yearly price chart for a specific year, click the corresponding bar in the Wave Viewer.''',

    '''When sold on {dr1} and purchased on {dr2} consistently year after year, our gain-loss bar chart reveals the percentage gains for {symbol} across each of the last {years} years.  Clicking on any bar in the Wave Viewer will reveal the price chart for that year.''',
]


t4_100 = [
    '''A close look at this table reveals a {pct_profitable} positive track record for {company}, with {num_winners} winners over the past {years} years and no losers. The highest climb was {biggest_winner}, and the average gain was {avg_profit}.''',

    '''The current table shows how {company} has performed over the past {years} years during the date range shown earlier.  You can see it had a {pct_profitable} win ratio, with {num_winners} winning years and zero losing years.  The biggest winner during that period gained {biggest_winner}, while the average gain was {avg_profit}.''',

    '''This table highlights how well {company} has done recently, going on a {pct_profitable} win streak over the past {years} years, with {num_winners} winning years during the given date range, and no losing years. At its peak, it gained {biggest_winner}, with the average gain settling in at {avg_profit}.''',

    '''Here’s a snapshot of the past {years} years for {company}.  It won {pct_profitable} of the time during the given date range, resulting in {num_winners} winners and zero losers.  The highest gain was {biggest_winner}, with the average gain clocking in at {avg_profit}.''',

    '''The table in front of you tells a compelling story: {company} had {num_winners} winning years and zero losses over the last {years} years. In other words, it was {pct_profitable} successful over the given date range for those years.  The highest gain was a respectable {biggest_winner}, with an average gain of {avg_profit}.''',
]
   
t4_less_100 = [

    '''Over the span of {years} years, {company} has had a {pct_profitable} positive track record when traded according to the date range given earlier. The table reports {num_winners} years in the win column, and {num_losers} in the loss column. Notable gains went as high as {biggest_winner}, while the average gain stood at {avg_profit}.''',

    '''This table illustrates the {years}-year performance for {company}. The success rate stands at {pct_profitable} in the green with {num_winners} good years and {num_losers} not-so-good year{s_losers}, when traded across the date range mentioned earlier. The best year boasted a {biggest_winner} gain, and the average gain was a respectable {avg_profit}.''',

    '''These numbers reveal a {pct_profitable} favorable history for {company} across the given date range for the previous {years} years. You'll find {num_winners} winning years and {num_losers} that didn't make the cut. The high point was a {biggest_winner} gain, with the average gain ringing in at {avg_profit}.''',

    '''Take a look at the track record for {company} over the previous {years} years. <break time="600ms"/> Had you gotten in and out every year over that same date range previously discussed, you would have been rewarded {pct_profitable} of the time, with {num_winners} years in the green and {num_losers} in the red. Your biggest gain would have been {biggest_winner}, and your average gain would have been {avg_profit}.''',

    '''History shows that {company} was {pct_profitable} successful over the past {years} years when traded during the specific date range given earlier.  Here's the breakdown: {num_winners} winning years and {num_losers} loser{s_losers}. The biggest gain was {biggest_winner}, and the average gain was {avg_profit}.'''
]


t5 = [
    '''A few more stats you might be interested in.  The median gain for this date range was {median_profit} with standard deviation of {stddev}.  The Cumulative Return for the historical years was {cumulative_return}. Trend Long and Trend Short show a {strength} {bullish_bearish_neutral} trend for the recent past.''',

    '''Here’s some more info to consider: <break time="600ms"/> {median_profit} is the median gain, {stddev} is the standard deviation, and the Cumulative Return over the past {years} years is {cumulative_return}. The latest trend has been {strength} {bullish_bearish_neutral}.''',

    '''Here are some more numbers to ponder: Median gain of {median_profit}, standard deviation at {stddev}, and a Cumulative Return of {cumulative_return} over {years} years. The recent trend leans {strength} {bullish_bearish_neutral}.''',

    '''Here are some more key metrics: Median gain for the time frame was {median_profit}, with a standard deviation of {stddev}. The {years} year Cumulative Return reached {cumulative_return}. Recent market moves show a {strength} {bullish_bearish_neutral} pattern.''',

    '''Let’s dig deeper into the numbers: Median gain sits at {median_profit}, and the standard deviation is {stddev}. The Cumulative Return was a solid {cumulative_return}. Recent trends indicate a {strength} {bullish_bearish_neutral} direction.''',
]


t6 = [
    '''The cumulative chart shows the annual historical capital growth of this wave over the past {years} years.  the cumulative growth at the end of {years} years was {cumulative_return}.''',

    '''Let’s take a look at the cumulative chart: It reveals a {years} years growth trend for this wave, capping off at {cumulative_return}.''',

    '''Don't miss the cumulative chart: It tracks this wave's capital growth trend over the last {years} years, ending at a noteworthy {cumulative_return} return.''',

    '''This wave's capital growth trend adds up to a {cumulative_return} cumulative return over {years} years, as seen on the cumulative chart.''',

    '''The cumulative chart is your chance to go back in time. If you had invested in this wave {years} years ago, and every year, got in and out during the given date range, your investment would be up {cumulative_return} today.''',
]


t7 = [
    '''The trend chart layers price data over the past {years} years to give you a composite picture of price action for {company}. It shows you when {symbol} typically surges or dips during specific date ranges.  This is key info for your investment strategies.<break time="300ms"/>''',

    '''With price data spanning the previous {years} years, the trend chart creates a layered view of potential entry and exit points for {symbol}.<break time="600ms"/> Use it to pinpoint when to dive into or out of a wave.''',

    '''By overlaying the last {years} years of price charts for {symbol}, the trend chart identifies repetitive patterns to help you identify potential wave entry and exit points.''',

    '''The trend chart stitches together the most recent {years} years of price data for {symbol}. This composite view helps you spot recurring patterns and date ranges for getting in and out of waves.''',

    '''Think of the trend chart as a {years} year history lesson for {symbol}. It overlays pricing data in a way that reveals repeating patterns to guide your wave entries and exits.''',
]






t8_facebook = [

    '''<break time="1000ms"/> Did you love what you just saw? Click the Like button and follow our page. You won't want to miss the next big trade we share. <break time="1500ms"/> At TradeWave, discover the year-round gems that traditional platforms overlook. Swing on over to Trade Wave dot AI and find the buried treasures that everyone else is missing.''',  

    '''<break time="1000ms"/> Don't miss the boat on our next wave of buried treasures. Click the Like button and follow our page to make sure you’re at the front of the line. <break time="1500ms"/> TradeWave exposes you to hidden potential, revealing secrets of how certain securities perform throughout the year, each and every year. Join us at TradeWave dot AI and unlock a treasure chest of opportunities.''',

    '''<break time="1000ms"/> If you found value in this video, click the Like button and follow our page so you don’t miss future reports like this one. <break time="1500ms"/> With TradeWave, you'll uncover buried treasures most traders will never see. Join us at TradeWave dot AI and join the league of savvy fortune finders.''',
   
    '''<break time="1000ms"/> Think this video was helpful? Click the Like button and follow our page. We’ve got more gems like this coming your way soon. <break time="1500ms"/> With TradeWave, you'll dig up date-specific buried treasures most traders will never see. Join us at TradeWave dot AI and you’ll never want to invest any other way.''',

    '''<break time="1000ms"/> If you like what you saw, click the Like button and follow our page. Be the first to snag the next buried treasure! <break time="1500ms"/> Don't just follow the market.<break time="300ms"/> stay ahead of it year-round with TradeWave. Join us at TradeWave dot AI and become the investor you always wanted to be.''',
]






t8_youtube = [

    '''<break time="1000ms"/> If this video got you excited to see more buried treasures, make sure to subscribe.  Then click the bell so you don’t miss a thing! <break time="1500ms"/> At TradeWave, discover the year-round gems that traditional platforms overlook. Swing on over to Trade Wave dot AI and find the potential rewards everyone else is missing.''',

    '''<break time="1000ms"/> If you liked this breakdown, you'll love what's coming. Smash that Subscribe button and ring the bell to stay updated. <break time="1500ms"/> TradeWave exposes you to hidden potential, revealing secrets of how certain securities perform throughout the year, each and every year. Join us at TradeWave dot AI and unlock the next treasure chest.''',

    '''<break time="1000ms"/> For more like this, subscribe and hit the bell. That’ll make sure you hear about future hidden treasures before everyone else does. <break time="1500ms"/> With TradeWave, you'll uncover date-specific buried treasures that most traders will never see. Join us at TradeWave dot AI and join the league of savvy fortune finders.''',

    '''<break time="1000ms"/> Want first dibs on our next video? Subscribe now and tap that bell to be the first to snag new hidden treasures. <break time="1500ms"/> With TradeWave, you'll dig up date-specific potential rewards that most traders never see. Join us at TradeWave dot AI and you’ll never want to invest any other way.''',

    '''<break time="1000ms"/> Don't let the next hidden treasure slip by. Subscribe now and click the bell for immediate updates. <break time="1500ms"/> Don't just follow the market.  <break time="300ms"/> Stay ahead of it year-round with TradeWave. Join us at TradeWave dot AI and become the investor you always wanted to be.''',
]




t9_cta = [

    '''<break time="1s"/> Every day, we showcase the Top 10 wave trades for that specific day.  Sign up for free and get them delivered straight to your inbox!  Click the link in the description to get started now.<break time="8s"/>''',
]



