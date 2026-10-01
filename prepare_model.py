"""Download public model files once, validate official checksum, prepare FFmpeg."""
import hashlib
import shutil
import urllib.request
from pathlib import Path

from engine import MODEL_NAME, ROOT

BASE = 'https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM'
HASH = '367074d6498f426d960b25f49531cf68'


def checksum(path):
    digest = hashlib.md5()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download(name, digest=None):
    folder = ROOT / 'models'
    folder.mkdir(exist_ok=True)
    target = folder / name
    if target.exists() and (not digest or checksum(target) == digest):
        print(name + ': ready', flush=True)
        return
    partial = target.with_suffix(target.suffix + '.part')
    print('Downloading ' + name, flush=True)
    with urllib.request.urlopen(BASE + '/' + name, timeout=90) as response, partial.open('wb') as f:
        total = int(response.headers.get('Content-Length', 0))
        done = 0
        last = -1
        while chunk := response.read(1024 * 1024):
            f.write(chunk)
            done += len(chunk)
            step = int(done / total * 10) if total else done // (100 * 1024 * 1024)
            if step != last:
                print(f'  {done / 1024**2:.0f} MB' + (f' / {total / 1024**2:.0f} MB' if total else ''), flush=True)
                last = step
    if digest and checksum(partial) != digest:
        raise RuntimeError('Model checksum failed: ' + str(partial))
    partial.replace(target)


def prepare_ffmpeg():
    import imageio_ffmpeg
    tools = ROOT / 'tools'
    tools.mkdir(exist_ok=True)
    shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), tools / 'ffmpeg.exe')
    print('FFmpeg: ready', flush=True)


if __name__ == '__main__':
    download(MODEL_NAME + '.ckpt', HASH)
    download(MODEL_NAME + '_tokenizer.model')
    prepare_ffmpeg()
