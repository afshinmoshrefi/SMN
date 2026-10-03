"""Compose a real presenter segment into complete, source-bound daily media."""
from pathlib import Path
import shutil

from daily_briefing import digest
import promotion_jobs as jobs
from subscription_writer import load_json,save_json,sha256
from speech_timing import validated_timing,at_offset
from video_render import probe,_run


def base_media(root,identifier,briefing,voice_id,ffprobe='ffprobe'):
    job=load_json(jobs._path(root,identifier));script=' '.join(b['text'] for b in briefing['script'])
    if job['kind']!='daily_briefing' or job['status']!='reviewed' or job.get('review_status')!='approved' or job['source_hash']!=digest(briefing):
        raise ValueError('An exact complete daily media job is required')
    names=('briefing.mp4','narration.mp3','narration.alignment.json','narration.receipt.json',
           'captions.vtt','transcript.txt','briefing.receipt.json')
    committed=job.get('review',{})
    if committed.get('payload_sha256')!=digest(job['inputs']):raise ValueError('Exact complete daily media review required')
    artifacts={item['name']:item['sha256'] for item in job['artifacts']}
    if any(committed.get('artifact_hashes',{}).get(name)!=artifacts.get(name) for name in names):raise ValueError('Master artifact review binding differs')
    files={name:jobs.get_artifact(root,identifier,name)[0] for name in names}
    audio=files['narration.mp3'];duration=float(probe(audio,ffprobe)['format']['duration'])
    timing=validated_timing(audio,script,duration,load_json(files['narration.alignment.json']))
    receipt=load_json(files['narration.receipt.json']);render=load_json(files['briefing.receipt.json'])
    if (receipt['voice_id']!=voice_id or receipt['audio_sha256']!=sha256(audio.read_bytes())
            or render['audio_sha256']!=receipt['audio_sha256'] or render['briefing_sha256']!=digest(briefing)
            or render['video_sha256']!=sha256(files['briefing.mp4'].read_bytes())
            or files['transcript.txt'].read_text('utf-8').strip()!=script
            or abs(float(probe(files['briefing.mp4'],ffprobe)['format']['duration'])-duration)>.15):
        raise ValueError('Complete daily audio/video/source custody differs')
    import html,re
    cues=[line for line in files['captions.vtt'].read_text('utf-8').splitlines() if line and line!='WEBVTT' and '-->' not in line and not line.isdigit()]
    if ' '.join(html.unescape(' '.join(cues)).split())!=' '.join(script.split()):raise ValueError('Master captions omit or change narration')
    return {'id':identifier,'files':files,'timing':timing,'duration':duration,'script':script,
            'audio_sha256':receipt['audio_sha256'],'voice_id':voice_id,'briefing_sha256':digest(briefing)}


def segment_audio(base,part,text,output,ffmpeg='ffmpeg'):
    script=base['script'];timing=base['timing']
    if not text or part not in {'intro','outro'} or not (script.startswith(text) if part=='intro' else script.endswith(text)):
        raise ValueError('Presenter segment must be the exact beginning or end of the script')
    first=0 if part=='intro' else len(script)-len(text);stop=first+len(text)
    if (first and not script[first-1].isspace()) or (stop<len(script) and not script[stop].isspace()):raise ValueError('Presenter text must end at a complete word boundary')
    start=0 if first==0 else at_offset(timing,first)
    end=base['duration'] if stop==len(script) else at_offset(timing,stop+len(script[stop:])-len(script[stop:].lstrip()))
    if not 0<end-start<=8:raise ValueError('Presenter segment must be brief and measured')
    output=Path(output);binding={'base_job_id':base['id'],'master_audio_sha256':base['audio_sha256'],
        'briefing_sha256':base['briefing_sha256'],'script':text,'part':part,'start':start,'end':end}
    receipt=output.with_suffix('.segment.json')
    if output.exists() or receipt.exists():
        old=load_json(receipt)
        if any(old.get(k)!=v for k,v in binding.items()) or old['audio_sha256']!=sha256(output.read_bytes()):
            raise ValueError('Existing immutable presenter audio differs')
        return old
    output.parent.mkdir(parents=True,exist_ok=True)
    _run([ffmpeg,'-nostdin','-v','error','-i',str(base['files']['narration.mp3']),'-ss',str(start),
          '-t',str(end-start),'-vn','-c:a','pcm_s16le',str(output)])
    binding['audio_sha256']=sha256(output.read_bytes());save_json(receipt,binding);return binding


def compose_avatar(output,base,avatar,segment,*,ffmpeg='ffmpeg',ffprobe='ffprobe'):
    """Keep the complete master narration; provider audio is never concatenated."""
    output,avatar=Path(output),Path(avatar)
    if output.exists():raise ValueError('Use a new immutable full-media revision')
    if any(segment[k]!=base[v] for k,v in [('base_job_id','id'),('master_audio_sha256','audio_sha256'),('briefing_sha256','briefing_sha256')]):
        raise ValueError('Presenter segment belongs to a different full narration')
    duration=float(probe(avatar,ffprobe)['format']['duration'])
    start,end=segment['start'],segment['end']
    if not 0<=start<end<=base['duration'] or not 0<duration<=8 or abs(duration-(end-start))>.15:
        raise ValueError('Presenter video must match the measured master audio interval')
    output.parent.mkdir(parents=True,exist_ok=True)
    # Fit rather than crop the actual provider image; disclosure is burned in.
    font='C:/Windows/Fonts/arial.ttf' if Path('C:/Windows/Fonts/arial.ttf').is_file() else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    font=font.replace('\\','/').replace(':','\\:')
    graph=(f'[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,'
           f'setpts=PTS-STARTPTS+{start}/TB[avatar];[0:v][avatar]overlay=eof_action=pass:repeatlast=0:'
           f"enable='gte(t,{start})*lt(t,{end})',drawtext=fontfile='{font}':text='AI-generated presenter':"
           f"fontsize=30:fontcolor=white:box=1:boxcolor=black@0.7:x=40:y=40:enable='gte(t,{start})*lt(t,{end})'[full]")
    _run([ffmpeg,'-nostdin','-v','error','-i',str(base['files']['briefing.mp4']),'-i',str(avatar),
          '-i',str(base['files']['narration.mp3']),'-filter_complex',graph,'-map','[full]','-map','2:a',
          '-c:v','libx264','-pix_fmt','yuv420p','-r','30','-c:a','aac','-t',str(base['duration']),
          '-movflags','+faststart',str(output)])
    _run([ffmpeg,'-nostdin','-v','error','-i',str(output),'-f','null','-'])
    if abs(float(probe(output,ffprobe)['format']['duration'])-base['duration'])>.15:raise ValueError('Complete narration duration changed')
    for name in ('narration.mp3','narration.alignment.json','narration.receipt.json','captions.vtt','transcript.txt'):
        shutil.copyfile(base['files'][name],output.parent/name)
    receipt={'media_role':'full_briefing','complete_narration':True,'avatar_generated':True,
        'base_job_id':base['id'],'briefing_sha256':base['briefing_sha256'],'audio_sha256':base['audio_sha256'],
        'script_sha256':sha256(base['script'].encode()),'captions_sha256':sha256((output.parent/'captions.vtt').read_bytes()),
        'avatar_video_sha256':sha256(avatar.read_bytes()),'video_sha256':sha256(output.read_bytes()),
        'duration_seconds':base['duration'],'avatar_interval':[start,end],'decoded':True,'review_status':'pending','publish':False}
    save_json(output.with_suffix('.receipt.json'),receipt);return receipt
