"""Layout bounds and local synthetic FFmpeg fixture; no provider or real likeness."""
from pathlib import Path
import shutil,tempfile,unittest
from PIL import Image
from subscription_writer import sha256
from video_render import article_layout,compose,_run,probe

IDENTITY='TradeWave study: ADP common stock (Nasdaq: ADP)\nLong | October 11–30, 2026 | 10 selected midterm-election years (1986–2022)\n9 of 10 finished higher; median full-window long result: 6.89%. Positive long results mean stock strength.\nSelected years, not consecutive. Compare the same window across histories; historical results are not future probabilities.\nSource: TradeWave engine; prices recorded through October 1, before the prospective entry.'
FONT=next((str(p) for p in [Path('C:/Windows/Fonts/arial.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')] if p.is_file()),None)

@unittest.skipUnless(FONT,'Readable local font required')
class Layout(unittest.TestCase):
    def test_complete_long_identity_stays_readable_and_outside_chart(self):
        layout=article_layout('ADP selected midterm history and the complete path through its seasonal window',IDENTITY,FONT)
        self.assertEqual(' '.join(layout['identity']['text'].split()),' '.join(IDENTITY.split()))
        self.assertGreaterEqual(layout['identity']['size'],30)
        self.assertLess(layout['chart_box'][3],layout['identity_y'])
        self.assertGreaterEqual(layout['chart_box'][3]-layout['chart_box'][1],700)
        self.assertLessEqual(layout['identity_y']+layout['identity']['height'],1865)
        with self.assertRaisesRegex(ValueError,'readable size'):article_layout('Title',IDENTITY*10,FONT)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg fixture required')
    def test_actual_fixture_decode_preserves_audio_chart_and_complete_captions(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);chart=root/'native.png';Image.new('RGB',(800,1200),'green').save(chart)
            audio=root/'fixture.mp3';_run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=440:duration=12','-c:a','libmp3lame',str(audio)])
            chart_before=chart.read_bytes();audio_before=audio.read_bytes();script='Synthetic fixture narration only.'
            timing={'audio_sha256':sha256(audio_before),'script_sha256':sha256(script.encode()),'method':'synthetic_fixture',
                    'segments':[{'text':script,'offset':0,'start':0,'end':11.8}]}
            receipt=compose(root/'output/pilot.mp4',audio,chart,script=script,title='ADP selected midterm history',
                            identity=IDENTITY,chart_sha256=sha256(chart_before),font=FONT,timing=timing)
            self.assertEqual(receipt['layout']['identity'],IDENTITY)
            self.assertEqual(chart.read_bytes(),chart_before);self.assertEqual(audio.read_bytes(),audio_before)
            self.assertTrue(receipt['decoded']);self.assertEqual(receipt['review_status'],'pending')
            self.assertEqual((root/'output/transcript.txt').read_text().strip(),script)
            self.assertIn(script,(root/'output/captions.vtt').read_text())
            self.assertTrue(10<=float(probe(root/'output/pilot.mp4')['format']['duration'])<=15)

if __name__=='__main__':unittest.main()
