"""Compose private portrait pilots from unchanged native charts and real audio."""
import json
from pathlib import Path
import subprocess
import textwrap

from subscription_writer import save_json, sha256, utc_now


def _run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise ValueError('Media runtime failed; inspect private render log')
    return result.stdout


def probe(path, ffprobe='ffprobe'):
    return json.loads(_run([ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))


def compose(output, audio, chart, *, script, title, identity, chart_sha256,
            ffmpeg='ffmpeg', ffprobe='ffprobe', font=None):
    """No financial calculations, fake narration, cropping or audio speed changes."""
    from PIL import Image, ImageDraw, ImageFont
    audio, chart, output = Path(audio), Path(chart), Path(output)
    if sha256(chart.read_bytes()) != chart_sha256:
        raise ValueError('Native chart changed since source review')
    metadata = probe(audio, ffprobe)
    duration = float(metadata['format']['duration'])
    if not 10 <= duration <= 15 or not any(s['codec_type'] == 'audio' for s in metadata['streams']):
        raise ValueError('Pilot requires actual complete narration lasting 10-15 seconds')
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
    receipt = {'status': 'rendered_pending_audio_visual_review', 'created_at': utc_now(),
        'duration_seconds': float(rendered['format']['duration']), 'chart_seconds': 6,
        'chart_sha256': chart_sha256, 'audio_sha256': sha256(audio.read_bytes()),
        'video_sha256': sha256(output.read_bytes()), 'script_sha256': sha256(script.encode()),
        'dimensions': [1080,1920], 'decoded': True, 'review_status': 'pending', 'publish': False}
    save_json(output.with_suffix('.receipt.json'), receipt)
    return receipt
