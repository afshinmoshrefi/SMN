# creates mp4 video from a series of png images and mp3s each associaed 

from moviepy.editor import VideoClip, AudioFileClip, clips_array,concatenate_videoclips
from moviepy.editor import *

# # Import the audio(Insert to location of your audio instead of audioClip.mp3)
# audio = AudioFileClip("test.mp3")
# # Import the Image and set its duration same as the audio (Insert the location of your photo instead of photo.jpg)
# clip = ImageClip("test.png").set_duration(audio.duration)
# # Set the audio of the clip
# clip = clip.set_audio(audio)
# # Export the clip

# clip.write_videofile("test.mp4", fps=24)

#----------------------------------------------------------------------------------------------------

def create_video_from_png_mp4_mp3(data):
    
    clips = []

    for t in data:
        png_path = t[0]
        mp3_path = t[1]

        visual_type = png_path[-3:]

        audio = AudioFileClip(mp3_path)

        if visual_type == 'png' or visual_type == 'jpg': # static image
            clip = ImageClip(png_path).set_duration(audio.duration)
            clip = clip.set_audio(audio)
        else:                   # video clip

            clip=VideoFileClip(png_path) # its really an mp4 not a png - just a bad var name
            # clip.set_audio (audio)
            existing_audio = clip.audio
            mixed_audio = CompositeAudioClip([existing_audio, audio])
            clip = clip.set_audio(mixed_audio)

        clip.audio_fadein(0.5).audio_fadeout(0.5) # to remove the poping audio glitch between some of the audio tracks when they are concatenated

        clips.append(clip)



    final_clip = concatenate_videoclips(clips,'compose')

    return final_clip
        



#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    # facebook_hashtags = '#TradingOpportunities #FinancialMarkets #Top10Reports #FreeOfCharge #ProfitableTrading #JoinUsNow #traders #investors #Stocks #ETF #Forex #Futures #forex #forextrader #ForexMarket #futurestrading #bonds #seasonal #StockMarketSeasonality #SeasonalPatterns #StockTrading #InvestingTips #FinancialAnalyst #StockPicking #TradeLikeAPro #StockMarketEducation #InvestorLife #MarketAnalysis'


    # today_date  = datetime.datetime.now().strftime("%Y-%m-%d")

    data = [
        ('/home/flask/blog/tmp/table1.png','/home/flask/blog/tmp/t1.mp3'),
        ('/home/flask/blog/tmp/table2.png','/home/flask/blog/tmp/t2.mp3'),
        # ('/home/flask/blog/tmp/table3.png','/home/flask/blog/tmp/t3.mp3'),
        ('/home/flask/blog/tmp/final_clip_youtube_automated_videos.mp4','/home/flask/blog/tmp/t8.mp3')

    ]

    data =[
        ('/var/www/html/wp-content/uploads/p/thumbnails/youtube/2023/2023-09-22/20-year-tradewave-report-heico-corp-hei-2023-09-22-to-2024-06-04.jpg','/home/flask/blog/tmp/t1.mp3'),
        ('/home/flask/blog/tmp/Disclaimer Slide 16-9 YT.png'                , '/home/flask/blog/tmp/t1_disclaimer.mp3'),
        ('/home/flask/blog/tmp/table1.png'                                  , '/home/flask/blog/tmp/t2.mp3') ,
        ('/home/flask/blog/tmp/bar_img.png'                                 , '/home/flask/blog/tmp/t3.mp3'),
        ('/home/flask/blog/tmp/table2.png'                                  , '/home/flask/blog/tmp/t4.mp3') ,
        ('/home/flask/blog/tmp/table3.png'                                  , '/home/flask/blog/tmp/t5.mp3') ,
        ('/home/flask/blog/tmp/cum_img.png'                                 , '/home/flask/blog/tmp/t6.mp3'),
        ('/home/flask/blog/tmp/sea_img.png'                                 , '/home/flask/blog/tmp/t7.mp3'),
        # ('/home/flask/blog/tmp/final_clip_youtube_automated_videos.mp4'     ,   '/home/flask/blog/tmp/t8.mp3')
    ]

    final_clip=video_file_path=create_video_from_png_mp4_mp3(data)
    final_clip.write_videofile("test.mp4", fps=24)

