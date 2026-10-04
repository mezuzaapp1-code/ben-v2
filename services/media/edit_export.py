"""Bounded deterministic export of an owned revision and client-rasterized text.

Text rasterization uses the user's actual browser fonts, bidi and CSS. Uploaded
PNGs are untrusted media, not executable SVG/HTML. They are decoded/re-encoded
locally; no client paths or URLs reach FFmpeg. No generation provider is called.
"""
import av
import base64
import hashlib
import io
import math
from pathlib import Path
import subprocess
import tempfile
import time
from PIL import Image
from services.media.contracts import MAX_VIDEO_BYTES
from services.media.edit_repository import error
from services.media.mobile_video import _SLOT
from services.media.video_storage import inspect_mp4

MAX_EXPORT_REQUEST = 24 * 1024 * 1024

def timeline(document):
    duration=document['source']['duration_seconds']
    body=document['body']
    points={0.0,round(duration,6)}
    for item in (*body['cues'],*body['textLayers']):
        points.update(round(min(duration,max(0,item[key])),6) for key in ('start','end'))
    ordered=sorted(points)
    return list(zip(ordered,ordered[1:]))

def validate_frames(payload,document,scratch,width,height):
    if not isinstance(payload,dict) or set(payload)!={'width','height','frames'}:
        raise ValueError('Invalid export')
    if (payload['width'],payload['height'])!=(width,height): raise ValueError('Dimensions mismatch')
    times=timeline(document)
    frames=payload['frames']
    if not isinstance(frames,list) or len(frames)!=len(times) or len(frames)>641:
        raise ValueError('Timeline mismatch')
    started=time.monotonic()
    total=0
    lines=['ffconcat version 1.0']
    for i,(item,(start,end)) in enumerate(zip(frames,times)):
        if not isinstance(item,dict) or set(item)!={'start','end','png'}: raise ValueError('Invalid frame')
        if any(isinstance(item[k],bool) or not isinstance(item[k],(float,int)) or not math.isfinite(item[k]) for k in ('start','end')):
            raise ValueError('Invalid timing')
        if abs(item['start']-start)>1e-6 or abs(item['end']-end)>1e-6: raise ValueError('Timeline mismatch')
        if not isinstance(item['png'],str) or len(item['png'])>3_000_000: raise ValueError('Image limit')
        if time.monotonic()-started>20: raise ValueError('Preparation time limit')
        data=base64.b64decode(item['png'],validate=True)
        total+=len(data)
        if total>16*1024*1024: raise ValueError('Image limit')
        with Image.open(io.BytesIO(data)) as img:
            if img.format!='PNG' or img.size!=(width,height) or getattr(img,'n_frames',1)!=1:
                raise ValueError('Invalid overlay')
            img.load()
            img.convert('RGBA').save(scratch/f'{i}.png')
        lines.extend([f"file '{i}.png'",'option framerate 1000',f'duration {end-start:.6f}'])
    lines.extend([f"file '{len(frames)-1}.png'",'option framerate 1000'])
    (scratch/'text.ffconcat').write_text('\n'.join(lines)+'\n',encoding='ascii')

def render(video,document,payload):
    if not _SLOT.acquire(blocking=False):
        raise error(503,'EDIT_EXPORT_BUSY','Video processing is busy. Please retry shortly.')
    try:
        with tempfile.TemporaryDirectory(prefix='ben-edit-export-') as directory:
            root=Path(directory)
            duration=document['source']['duration_seconds']
            # Source dimensions are verified server-side; client dimensions are only claims.
            with av.open(io.BytesIO(video),format='mp4',options={'protocol_whitelist':''}) as c:
                v=c.streams.video[0]; width,height=v.width,v.height
                rate=v.average_rate
                if (width,height) not in ((1280,720),(720,1280)) or not rate or not 0<float(rate)<=30.1:
                    raise ValueError('Unsupported source')
                audio=bool(c.streams.audio)
            ratio='16:9' if width>height else '9:16'
            inspect_mp4(video,aspect_ratio=ratio,duration_seconds=duration,profile='mobile-v1')
            validate_frames(payload,document,root,width,height)
            source,output=root/'source.mp4',root/'export.mp4'
            source.write_bytes(video)
            args=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n','-xerror',
                '-threads','2','-protocol_whitelist','file','-i',str(source),
                '-threads','1','-protocol_whitelist','file','-f','concat','-safe','0','-i',str(root/'text.ffconcat'),
                '-filter_complex_threads','1','-filter_complex',
                '[0:v]setpts=PTS-STARTPTS[v];[1:v]setpts=PTS-STARTPTS[t];[v][t]overlay=0:0:format=auto,format=yuv420p[out]',
                '-map','[out]','-map','0:a:0?','-map_metadata','-1','-map_chapters','-1',
                '-c:v','libx264','-threads','2','-preset','veryfast','-crf','18',
                '-r',str(rate),'-t',str(duration),'-c:a','copy','-movflags','+faststart',
                '-fs',str(MAX_VIDEO_BYTES),str(output)]
            subprocess.run(args,check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120)
            if output.stat().st_size>=MAX_VIDEO_BYTES: raise ValueError('Output limit')
            result=output.read_bytes()
            checked=inspect_mp4(result,aspect_ratio=ratio,duration_seconds=duration,profile='mobile-v1')
            if checked[3]!=audio or source.read_bytes()!=video: raise ValueError('Source mismatch')
            return result
    except (ValueError,OSError,SyntaxError,IndexError,av.FFmpegError,subprocess.SubprocessError):
        raise error(422,'EDIT_EXPORT_FAILED','Export could not be completed. Your saved edit and original video are unchanged.') from None
    finally:
        _SLOT.release()

async def export_revision(service,org,user,document_id,revision_id,payload):
    import asyncio, uuid
    revision=await service.repo.read(org,user,document_id,revision_id)
    document=revision['document']
    data=await service.reader(service.repo.media,org,user,uuid.UUID(document['source']['resource_id']))
    if hashlib.sha256(data).hexdigest()!=revision['source_checksum']:
        raise error(409,'EDIT_SOURCE_CHANGED','Source verification failed')
    output=await asyncio.to_thread(render,data,document,payload)
    # Revocation/source checks run again after processing, before any delivery.
    await service.repo.read(org,user,document_id,revision_id)
    return output
