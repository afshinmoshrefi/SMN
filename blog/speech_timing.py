"""Bind narration timing to exact audio and script before rendering captions/cards."""
import math
import re
from pathlib import Path

from subscription_writer import load_json, sha256


def provider_timing(script, audio, alignment):
    if not isinstance(alignment, dict) or ''.join(alignment.get('characters', [])) != script:
        raise ValueError('Provider alignment does not match the approved script')
    starts = alignment.get('character_start_times_seconds', [])
    ends = alignment.get('character_end_times_seconds', [])
    if len(starts) != len(script) or len(ends) != len(script):
        raise ValueError('Incomplete narration alignment')
    previous = 0.0
    for start, end in zip(starts, ends):
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or not previous <= start <= end:
            raise ValueError('Invalid narration timing')
        previous = start
    segments = []
    # Sentence boundaries also keep briefing cards aligned to their narration.
    for sentence in re.finditer(r'\S[\s\S]*?(?:[.!?](?=\s|$)|$)', script):
        words = list(re.finditer(r'\S+', sentence.group()))
        first = 0
        for i, word in enumerate(words):
            start = sentence.start() + words[first].start()
            stop = sentence.start() + word.end()
            if i == len(words) - 1 or (sentence.start() + words[i + 1].end() - start > 76):
                segments.append({'text': script[start:stop], 'offset': start,
                                 'start': starts[start], 'end': ends[stop - 1]})
                first = i + 1
    return {'audio_sha256': sha256(audio), 'script_sha256': sha256(script.encode()),
            'method': 'elevenlabs_character_alignment', 'segments': segments,
            'character_start_times_seconds': starts}


def validated_timing(audio, script, duration, timing=None):
    if timing is None:
        path = Path(audio).with_suffix('.alignment.json')
        if not path.is_file():
            raise ValueError('Measured narration timing is required before rendering')
        timing = load_json(path)
    if timing.get('audio_sha256') != sha256(Path(audio).read_bytes()) or timing.get('script_sha256') != sha256(script.encode()):
        raise ValueError('Narration timing belongs to different audio or script')
    segments = timing.get('segments', [])
    cursor, previous = 0, 0.0
    for segment in segments:
        offset, text = segment.get('offset'), segment.get('text')
        start, end = segment.get('start'), segment.get('end')
        if type(offset) is not int or not isinstance(text, str) or not text or offset < cursor or script[cursor:offset].strip() or script[offset:offset + len(text)] != text:
            raise ValueError('Caption text differs from the complete approved script')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or not previous <= start < end <= duration + .1:
            raise ValueError('Caption timing falls outside the audio or overlaps')
        cursor, previous = offset + len(text), end
    if not segments or script[cursor:].strip():
        raise ValueError('Captions omit narration')
    return timing


def at_offset(timing, offset):
    starts = timing.get('character_start_times_seconds')
    if starts is not None:
        return starts[offset]
    return next(s['start'] for s in timing['segments'] if s['offset'] == offset)


def write_captions(path, timing):
    import html
    import textwrap
    def stamp(value):
        ms = round(value * 1000)
        return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}'
    cues = [stamp(s['start']) + ' --> ' + stamp(s['end']) + '\n' +
            '\n'.join(textwrap.wrap(html.escape(s['text']), 42)) for s in timing['segments']]
    Path(path).write_text('WEBVTT\n\n' + '\n\n'.join(cues) + '\n', encoding='utf-8')
