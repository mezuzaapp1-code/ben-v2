"""Bounded metadata probe plus full decode; no raw diagnostics escape."""
import json
from pathlib import Path
import subprocess
import tempfile

from services.media.video_storage import inspect_mp4
from services.media.contracts import MAX_VIDEO_BYTES


def validate(data):
    if not data or len(data) > MAX_VIDEO_BYTES:
        raise ValueError('short_output_size')
    try:
        with tempfile.TemporaryDirectory(prefix='ben-short-') as directory:
            path = Path(directory) / 'output.mp4'
            path.write_bytes(data)
            result = subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file',
                '-show_entries','stream=codec_name,width,height,duration,codec_type',
                '-of','json',str(path)], capture_output=True, check=True, timeout=15)
            if len(result.stdout) > 16000:
                raise ValueError('short_probe_size')
            streams = json.loads(result.stdout)['streams']
            if sorted(s['codec_name'] for s in streams) != ['aac','h264']:
                raise ValueError('short_streams')
        width, height, seconds, audio = inspect_mp4(data, aspect_ratio='9:16',
            duration_seconds=35, profile='short-v1')
        return {'width':width,'height':height,'duration_seconds':seconds,
                'has_audio':audio,'video_codec':'h264','audio_codec':'aac','full_decode':True}
    except (subprocess.SubprocessError, OSError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError('short_probe_failed') from None
