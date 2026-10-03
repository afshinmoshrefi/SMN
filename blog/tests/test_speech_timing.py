import base64
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import elevenlabs_client as eleven
from speech_timing import provider_timing, validated_timing, write_captions, at_offset
from subscription_writer import load_json, sha256


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.audio = self.root / 'narration.mp3'
        self.audio.write_bytes(b'actual-audio-fixture')
        self.script = 'One complete claim. A second story.'
        self.alignment = {'characters': list(self.script),
                          'character_start_times_seconds': [i * .1 for i in range(len(self.script))],
                          'character_end_times_seconds': [(i + 1) * .1 for i in range(len(self.script))]}

    def test_provider_exact_timing_drives_captions_and_story_boundaries(self):
        timing = provider_timing(self.script, self.audio.read_bytes(), self.alignment)
        validated_timing(self.audio, self.script, 4, timing)
        self.assertEqual(at_offset(timing, self.script.index('A second')), 2.0)
        write_captions(self.root / 'captions.vtt', timing)
        text = (self.root / 'captions.vtt').read_text()
        self.assertIn('00:00:02.000 --> 00:00:03.500', text)
        self.assertEqual(len(timing['segments']), 2)

    def test_mismatched_audio_script_missing_text_and_overlapping_cues_fail(self):
        original = provider_timing(self.script, self.audio.read_bytes(), self.alignment)
        for field, value in [('audio_sha256', 'wrong'), ('script_sha256', 'wrong')]:
            timing = dict(original, **{field: value})
            with self.assertRaises(ValueError): validated_timing(self.audio, self.script, 4, timing)
        timing = copy.deepcopy(original); timing['segments'][1]['start'] = .2
        with self.assertRaises(ValueError): validated_timing(self.audio, self.script, 4, timing)
        timing = copy.deepcopy(original); timing['segments'].pop()
        with self.assertRaises(ValueError): validated_timing(self.audio, self.script, 4, timing)
        with self.assertRaises(ValueError): validated_timing(self.audio, self.script, 4)

    def test_paid_audio_and_alignment_saved_once_and_reused(self):
        identity = eleven.request_identity(self.script, 'voice', 'model', {})
        quote = {'request_sha256': identity, 'verified': True, 'verified_at': '2026-10-03', 'credits': 35}
        out = self.root / 'generated.mp3'
        payload = {'audio_base64': base64.b64encode(self.audio.read_bytes()).decode(), 'alignment': self.alignment}
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture'}), patch.object(eleven, '_request', return_value=(payload, {'character-cost': '35'})) as request:
            receipt = eleven.speech(self.root, self.script, 'voice', 'model', {}, quote, out)
            eleven.speech(self.root, self.script, 'voice', 'model', {}, quote, out)
            self.assertEqual(request.call_count, 1)
            self.assertIn('/with-timestamps?', request.call_args.args[0])
            self.assertEqual(receipt['audio_sha256'], sha256(self.audio.read_bytes()))
            validated_timing(out, self.script, 4)

    def test_missing_vendor_alignment_holds_composition_without_rebuying_audio(self):
        identity = eleven.request_identity(self.script, 'voice', 'model', {})
        quote = {'request_sha256': identity, 'verified': True, 'verified_at': '2026-10-03', 'credits': 35}
        out = self.root / 'generated.mp3'
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture'}), patch.object(eleven, '_request', return_value=({'audio_base64': 'YXVkaW8=', 'alignment': None}, {})) as request:
            receipt = eleven.speech(self.root, self.script, 'voice', 'model', {}, quote, out)
            self.assertEqual(receipt['timing_status'], 'needs_alignment_review')
            self.assertEqual(load_json(self.root / 'elevenlabs-budget.json')['reservations'][0]['status'], 'received')
            with self.assertRaises(ValueError): validated_timing(out, self.script, 4)
            eleven.speech(self.root, self.script, 'voice', 'model', {}, quote, out)
            self.assertEqual(request.call_count, 1)


if __name__ == '__main__': unittest.main()
