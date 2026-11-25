"""One-time installation of the public diarization model files."""
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from speakers import MODEL_DIR, MODEL_HASHES, model_hash

BASE = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/'
SEGMENTATION = 'speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2'
EMBEDDING = 'speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx'
ARCHIVE_MEMBER = 'sherpa-onnx-pyannote-segmentation-3-0/model.onnx'


def download(url, target):
    with urllib.request.urlopen(BASE + url, timeout=90) as source, target.open('wb') as output:
        shutil.copyfileobj(source, output)


def prepare():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='install-', dir=MODEL_DIR) as folder:
        for name, expected in MODEL_HASHES.items():
            target = MODEL_DIR / name
            if target.is_file() and model_hash(target) == expected:
                print(name + ': ready', flush=True)
                continue
            print('Downloading ' + name, flush=True)
            staged = Path(folder) / name
            if name == 'segmentation.onnx':
                archive = Path(folder) / 'segmentation.tar.bz2'
                download(SEGMENTATION, archive)
                with tarfile.open(archive, 'r:bz2') as package:
                    member = package.getmember(ARCHIVE_MEMBER)
                    if not member.isfile():
                        raise RuntimeError('Unexpected segmentation archive member')
                    with package.extractfile(member) as source, staged.open('wb') as output:
                        shutil.copyfileobj(source, output)
            else:
                download(EMBEDDING, staged)
            if model_hash(staged) != expected:
                raise RuntimeError('Speaker model checksum failed: ' + name)
            staged.replace(target)
    print('Speaker models ready for offline use.', flush=True)


if __name__ == '__main__':
    prepare()
