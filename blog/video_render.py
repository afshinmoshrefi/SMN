"""Compose private portrait pilots from unchanged native charts and real audio."""
import json
from pathlib import Path
import subprocess
import textwrap

from subscription_writer import save_json, sha256, utc_now
from speech_timing import validated_timing, write_captions, at_offset


def _run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise ValueError('Media runtime failed; inspect private render log')
    return result.stdout


def probe(path, ffprobe='ffprobe'):
    return json.loads(_run([ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))


def compose(output, audio, chart, *, script, title, identity, chart_sha256,
            ffmpeg='ffmpeg', ffprobe='ffprobe', font=None, timing=None):
    """No financial calculations, fake narration, cropping or audio speed changes."""
    from PIL import Image, ImageDraw, ImageFont
    audio, chart, output = Path(audio), Path(chart), Path(output)
    if sha256(chart.read_bytes()) != chart_sha256:
        raise ValueError('Native chart changed since source review')
    metadata = probe(audio, ffprobe)
    duration = float(metadata['format']['duration'])
    if not 10 <= duration <= 15 or not any(s['codec_type'] == 'audio' for s in metadata['streams']):
        raise ValueError('Pilot requires actual complete narration lasting 10-15 seconds')
    timing = validated_timing(audio, script, duration, timing)
    if output.exists():
        raise ValueError('Render output is immutable; use a new revision')
    output.parent.mkdir(parents=True, exist_ok=True)
    fonts = [font, 'C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    font_path = next((p for p in fonts if p and Path(p).is_file()), None)
    if not font_path:
        raise ValueError('A readable font must be configured')
    large, small = ImageFont.truetype(font_path, 62), ImageFont.truetype(font_path, 37)
    native = Image.open(chart).convert('RGB')
    native.thumbnail((1000, 1350))
    def frame(name, opening=False, closing=False):
        image = Image.new('RGB', (1080, 1920), '#101b2a'); draw = ImageDraw.Draw(image)
        draw.text((55, 55), 'TradeWave | Archival study', font=small, fill='#9ee1cf')
        draw.multiline_text((55, 135), '\n'.join(textwrap.wrap(title, 26)), font=large, fill='white', spacing=12)
        if not opening and not closing:
            image.paste(native, ((1080-native.width)//2, 390))
        else:
            message = 'History has a path.\nUnderstand the risks.' if opening else 'Read the full study\nfor context and risks.'
            draw.multiline_text((55, 760), message, font=large, fill='white', spacing=20)
        draw.multiline_text((55, 1640), '\n'.join(textwrap.wrap(identity, 46)), font=small, fill='white', spacing=10)
        path = output.parent / name; image.save(path); return path
    opening, middle, closing = frame('opening.png', opening=True), frame('chart.png'), frame('closing.png', closing=True)
    # Six seconds for the complete native chart; the remaining real audio is split around it.
    lead = (duration - 6) / 2
    timeline = output.parent / 'timeline.txt'
    timeline.write_text(''.join("file '" + p.name + "'\nduration " + str(t) + '\n' for p,t in
        ((opening, lead), (middle, 6), (closing, lead))) + "file 'closing.png'\n", encoding='utf-8')
    _run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'concat', '-safe', '1', '-i', str(timeline),
        '-i', str(audio), '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-r', '30', '-c:a', 'aac', '-t', str(duration), '-movflags', '+faststart', str(output)])
    _run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(output), '-f', 'null', '-'])
    rendered = probe(output, ffprobe)
    video = next(s for s in rendered['streams'] if s['codec_type'] == 'video')
    if (video['width'], video['height'], video['codec_name']) != (1080,1920,'h264'):
        raise ValueError('Rendered video contract differs')
    (output.parent / 'transcript.txt').write_text(script + '\n', encoding='utf-8')
    write_captions(output.parent/'captions.vtt', timing)
    receipt = {'status': 'rendered_pending_audio_visual_review', 'created_at': utc_now(),
        'duration_seconds': float(rendered['format']['duration']), 'chart_seconds': 6,
        'chart_sha256': chart_sha256, 'audio_sha256': sha256(audio.read_bytes()),
        'video_sha256': sha256(output.read_bytes()), 'script_sha256': sha256(script.encode()),
        'dimensions': [1080,1920], 'decoded': True, 'caption_timing':timing['method'],
        'review_status': 'pending', 'publish': False}
    save_json(output.with_suffix('.receipt.json'), receipt)
    return receipt


def compose_briefing(output,audio,briefing,review,*,ffmpeg='ffmpeg',ffprobe='ffprobe',font=None,timing=None):
    """Real narration with source-labeled cards; no avatar is implied or substituted."""
    from daily_briefing import inspect,digest
    from PIL import Image,ImageDraw,ImageFont
    validation=inspect(briefing,review)
    if validation['review_status']!='approved' or validation['issues']:
        raise ValueError('Exact daily source/script review required')
    audio,output=Path(audio),Path(output)
    duration=float(probe(audio,ffprobe)['format']['duration'])
    script=' '.join(b['text'] for b in briefing['script'])
    timing=validated_timing(audio,script,duration,timing)
    if not 0<duration<=180:raise ValueError('Briefing narration duration exceeds private prototype contract')
    if output.exists():raise ValueError('Immutable media revision already exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    fonts=[font,'C:/Windows/Fonts/arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    font_path=next((p for p in fonts if p and Path(p).is_file()),None)
    if not font_path:raise ValueError('Readable font must be configured')
    large,small=ImageFont.truetype(font_path,60),ImageFont.truetype(font_path,36)
    claims={c['id']:c for c in briefing['claims']}; sources={s['id']:s for s in briefing['sources']}
    paths=[]; starts=[]; offset=0
    for index,block in enumerate(briefing['script']):
        starts.append(0 if index == 0 else at_offset(timing,offset))
        offset+=len(block['text'])+1
        image=Image.new('RGB',(1080,1920),'#101b2a');draw=ImageDraw.Draw(image)
        draw.text((60,60),'Seasonal Market News',font=small,fill='#9ee1cf')
        draw.text((60,140),briefing['edition_date']+' | Market Wrap',font=small,fill='white')
        draw.multiline_text((60,420),'\n'.join(textwrap.wrap(block['text'],30)),font=large,fill='white',spacing=18)
        ids={support['source_id'] for cid in block['claim_ids'] for support in claims[cid]['supports']}
        labels='Sources: '+', '.join(sorted({sources[s]['publisher'] for s in ids}))
        draw.multiline_text((60,1660),'\n'.join(textwrap.wrap(labels,43)),font=small,fill='#9ee1cf',spacing=10)
        path=output.parent/f'card-{index+1}.png';image.save(path);paths.append(path)
    timeline=output.parent/'briefing-timeline.txt'
    ends=starts[1:]+[duration]
    timeline.write_text(''.join("file '"+p.name+"'\nduration "+str(end-start)+'\n' for p,start,end in zip(paths,starts,ends))+
        "file '"+paths[-1].name+"'\n",encoding='utf-8')
    _run([ffmpeg,'-nostdin','-v','error','-f','concat','-safe','1','-i',str(timeline),'-i',str(audio),
        '-map','0:v','-map','1:a','-c:v','libx264','-pix_fmt','yuv420p','-r','30','-c:a','aac',
        '-t',str(duration),'-movflags','+faststart',str(output)])
    _run([ffmpeg,'-nostdin','-v','error','-i',str(output),'-f','null','-'])
    metadata=probe(output,ffprobe); script=' '.join(b['text'] for b in briefing['script'])
    (output.parent/'transcript.txt').write_text(script+'\n',encoding='utf-8')
    write_captions(output.parent/'captions.vtt',timing)
    receipt={'status':'rendered_pending_audio_visual_review','briefing_sha256':digest(briefing),
        'audio_sha256':sha256(audio.read_bytes()),'video_sha256':sha256(output.read_bytes()),
        'duration_seconds':float(metadata['format']['duration']),'dimensions':[1080,1920],'decoded':True,
        'avatar_generated':False,'card_timing':{'method':timing['method'],'starts':starts},
        'caption_timing':timing['method'],'review_status':'pending','publish':False}
    save_json(output.with_suffix('.receipt.json'),receipt);return receipt
