# pip install google-cloud-texttospeech

import os
from unicodedata import name
from urllib import response
from google.cloud import texttospeech_v1

key_file = '/home/flask/blog/text-to-speech-399114-54f6520c7b2b.json'
os.environ['GOOGLE_APPLICATION_CREDENTIALS']=key_file

client = texttospeech_v1.TextToSpeechClient()

test_text = '''<speak>Over the next few minutes, you'll be introduced to the fundamental features of the TradeWave Viewer on a desktop browser.</speak>'''

voice1 = texttospeech_v1.VoiceSelectionParams(
    language_code = 'en-US',
    name = 'en-US-Wavenet-J'
)
voice2 = texttospeech_v1.VoiceSelectionParams(
    language_code = 'en-US',
    name = 'en-US-Studio-M'
)
voice3 = texttospeech_v1.VoiceSelectionParams(
    language_code = 'en-US',
    name = 'en-US-Standard-J'
)
voice4 = texttospeech_v1.VoiceSelectionParams(
    language_code = 'en-US',
    name = 'en-US-News-N'
)
voice5 = texttospeech_v1.VoiceSelectionParams(
    language_code = 'en-US',
    name = 'en-US-News-M'
)
voice6 = texttospeech_v1.VoiceSelectionParams(
    language_code = 'en-US',
    name = 'en-US-Neural2-I'
)

voice = voice2

#-------------------------------------------------------------------------------------------------------------
def text_to_mp3(text,mp3_filepath):


    # return  # remove its for debug so that mp3s are not recreated everytime video is made

    synthesis_input = texttospeech_v1.SynthesisInput(ssml=text)

    audio_config = texttospeech_v1.AudioConfig(
        audio_encoding = texttospeech_v1.AudioEncoding.MP3
    )

    response1 = client.synthesize_speech(
        input = synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    with open(mp3_filepath,'wb') as output:
        output.write(response1.audio_content)


#####################################################################################################################
################################################   Main Program  ####################################################
#####################################################################################################################

if __name__ == '__main__':

    text = test_text
    
    filepath = '/home/flask/blog/test.mp3'

    text_to_mp3(text,filepath)