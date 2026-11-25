"""Optional local processing for speaker turns in normalized mono audio."""
import hashlib
import json
import subprocess
import sys
import tempfile
import wave
from importlib.util import find_spec
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / 'models' / 'speakers'
MODEL_HASHES = {
    'segmentation.onnx': '220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079',
    'embedding.onnx': 'f1ee61be04000b50392a63442e5dd9feaea7e81dddd494d3647cf1c3d88a1498',
}
SETUP_HINT = 'Чтобы включить разделение по говорящим, один раз запустите Prepare-speakers.cmd с интернетом.'


def available():
    return find_spec('sherpa_onnx') is not None and all((MODEL_DIR / name).is_file() for name in MODEL_HASHES)


def model_hash(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def detect_speakers(wav, count, stop, on_status):
    from engine import Cancelled

    if not available():
        raise RuntimeError(SETUP_HINT)
    if count not in (-1, *range(2, 11)):
        raise ValueError('Укажите от 2 до 10 спикеров или выберите «Авто».')
    if stop.is_set():
        raise Cancelled()

    executable = Path(sys.executable)
    if executable.name.lower() == 'pythonw.exe':
        executable = executable.with_name('python.exe')
    on_status('Определяю говорящих...')
    with tempfile.TemporaryDirectory(prefix='gigaam-speakers-') as folder, tempfile.TemporaryFile() as errors:
        result = Path(folder) / 'turns.json'
        args = [str(executable), '-X', 'utf8', str(Path(__file__).resolve()), str(wav), str(count), str(result)]
        worker = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=errors,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while worker.poll() is None:
                if stop.wait(0.1):
                    raise Cancelled()
            if stop.is_set():
                raise Cancelled()
            if worker.returncode != 0:
                errors.seek(0)
                raise RuntimeError('Не удалось разделить голоса.\n' + errors.read(3500).decode('utf-8', 'replace'))
            return [tuple(turn) for turn in json.loads(result.read_text(encoding='utf-8'))]
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.wait()


def process_wave(wav, count):
    from engine import RATE

    import numpy as np
    import sherpa_onnx as sherpa

    for name, expected in MODEL_HASHES.items():
        if model_hash(MODEL_DIR / name) != expected:
            raise RuntimeError('Файл модели спикеров повреждён. ' + SETUP_HINT)

    config = sherpa.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(MODEL_DIR / 'segmentation.onnx')),
            num_threads=2),
        embedding=sherpa.SpeakerEmbeddingExtractorConfig(
            model=str(MODEL_DIR / 'embedding.onnx'), num_threads=2),
        clustering=sherpa.FastClusteringConfig(num_clusters=count),
        min_duration_on=0.2, min_duration_off=0.25)
    diarizer = sherpa.OfflineSpeakerDiarization(config)
    with wave.open(str(wav), 'rb') as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, RATE):
            raise ValueError('Для разделения спикеров нужен WAV: моно, 16 кГц, 16 бит.')
        samples = np.empty(audio.getnframes(), dtype=np.float32)
        start = 0
        while pcm := audio.readframes(RATE * 10):
            values = np.frombuffer(pcm, dtype='<i2')
            end = start + values.size
            np.multiply(values, np.float32(1 / 32768), out=samples[start:end])
            start = end
    if not start:
        return []

    result = diarizer.process(samples[:start])
    return [(turn.start, turn.end, turn.speaker) for turn in result.sort_by_start_time()]


if __name__ == '__main__':
    source, count, output = sys.argv[1:]
    try:
        turns = process_wave(source, int(count))
        Path(output).write_text(json.dumps(turns), encoding='utf-8')
    except Exception as exc:
        sys.exit(str(exc))
