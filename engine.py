"""Local file transcription. No network calls during recognition."""
import array
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from transcript import Segment

ROOT = Path(__file__).resolve().parent
MODEL_NAME = 'v3_e2e_ctc'
SUPPORTED_MODELS = ('v3_e2e_ctc', 'v3_e2e_rnnt')
RATE = 16000


class Cancelled(Exception):
    pass


@dataclass
class Chunk:
    pcm: bytes
    frames: int
    end_frame: int
    total_frames: int

    @property
    def start_frame(self):
        return self.end_frame - self.frames


def iter_chunks(path):
    """Read bounded chunks, cut near low-energy audio, keep every sample once."""
    with wave.open(str(path), 'rb') as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, RATE):
            raise ValueError('Требуется WAV: моно, 16 кГц, 16 бит.')
        total = audio.getnframes()
        if not total:
            raise ValueError('В файле нет аудио.')
        while audio.tell() < total:
            start = audio.tell()
            pcm = audio.readframes(24 * RATE)
            count = len(pcm) // 2
            cut = count
            if start + count < total:
                samples = array.array('h', pcm)
                if sys.byteorder != 'little':
                    samples.byteswap()
                window = RATE // 10
                candidates = range(20 * RATE, count - window, window)
                quiet = min(candidates, key=lambda i: sum(x * x for x in samples[i:i + window:8]))
                cut = quiet + window // 2
            audio.setpos(start + cut)
            yield Chunk(pcm[:cut * 2], cut, start + cut, total)


def pack_speech_ranges(ranges, total_frames):
    """Group nearby speech while keeping absolute offsets and the 24s limit."""
    packed = []
    for start, end in sorted(ranges):
        start, end = max(0, int(start)), min(total_frames, int(end))
        if end <= start:
            continue
        if packed and start <= packed[-1][1]:
            # Overlap is not sent to recognition twice.
            packed[-1] = (packed[-1][0], max(packed[-1][1], end))
        elif packed and start - packed[-1][1] <= RATE and end - packed[-1][0] <= 24 * RATE:
            packed[-1] = (packed[-1][0], end)
        else:
            packed.append((start, end))
    return packed


def iter_speech_chunks(path, ranges):
    with wave.open(str(path), 'rb') as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, RATE):
            raise ValueError('Требуется WAV: моно, 16 кГц, 16 бит.')
        total = audio.getnframes()
        if not total:
            raise ValueError('В файле нет аудио.')
        for start, end in pack_speech_ranges(ranges, total):
            while start < end:
                audio.setpos(start)
                pcm = audio.readframes(min(24 * RATE, end - start))
                count = len(pcm) // 2
                if not count:
                    raise ValueError('Аудиофайл повреждён или обрезан.')
                cut = count
                if start + count < end:
                    samples = array.array('h', pcm)
                    if sys.byteorder != 'little':
                        samples.byteswap()
                    window = RATE // 10
                    candidates = range(20 * RATE, count - window, window)
                    quiet = min(candidates, key=lambda i: sum(x * x for x in samples[i:i + window:8]))
                    cut = quiet + window // 2
                yield Chunk(pcm[:cut * 2], cut, start + cut, total)
                start += cut


def detect_speech_ranges(path, vad_model, stop, on_status):
    """Run bundled Silero locally in 32ms frames without loading the whole file."""
    import torch
    from silero_vad.utils_vad import get_speech_timestamps_from_probs
    probabilities = []
    vad_model.reset_states()
    with wave.open(str(path), 'rb') as audio, torch.inference_mode():
        total = audio.getnframes()
        if not total:
            raise ValueError('В файле нет аудио.')
        last_percent = -1
        while pcm := audio.readframes(512):
            if stop.is_set():
                raise Cancelled()
            samples = torch.frombuffer(bytearray(pcm), dtype=torch.int16).float() / 32768.0
            if len(samples) < 512:
                samples = torch.nn.functional.pad(samples, (0, 512 - len(samples)))
            probabilities.append(vad_model(samples, RATE).item())
            percent = int(audio.tell() / total * 100)
            if percent != last_percent:
                on_status(f'Ищу речь и паузы: {percent}%')
                last_percent = percent
    ranges = get_speech_timestamps_from_probs(
        probabilities, sampling_rate=RATE, audio_length_samples=total,
        threshold=0.45, min_speech_duration_ms=160, min_silence_duration_ms=350,
        speech_pad_ms=200, max_speech_duration_s=22,
        min_silence_at_max_speech=150, use_max_poss_sil_at_max_speech=True)
    return [(r['start'], r['end']) for r in ranges]


def transcribe_wav(path, model, stop, on_part, speech_ranges=None, on_segment=None):
    texts = []
    iterator = iter_chunks(path) if speech_ranges is None else iter_speech_chunks(path, speech_ranges)
    if speech_ranges is not None:
        with wave.open(str(path), 'rb') as audio:
            total_speech = sum(end - start for start, end in pack_speech_ranges(speech_ranges, audio.getnframes()))
    else:
        with wave.open(str(path), 'rb') as audio:
            total_speech = audio.getnframes()
    processed = 0
    with tempfile.TemporaryDirectory(prefix='gigaam-clips-') as directory:
        clip = Path(directory) / 'clip.wav'
        for chunk in iterator:
            if stop.is_set():
                raise Cancelled()
            with wave.open(str(clip), 'wb') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(RATE)
                f.writeframes(chunk.pcm)
            result = model.transcribe(str(clip))
            text = str(getattr(result, 'text', result)).strip()
            # Keep all generated UI/output text consistent with the user's preference.
            text = text.replace('\u2014', '-').replace('\u2013', '-')
            if text:
                texts.append(text)
                if on_segment is not None:
                    on_segment(Segment(chunk.start_frame / RATE, chunk.end_frame / RATE, text))
            processed += chunk.frames
            on_part(text, processed / total_speech * 100)
    if stop.is_set():
        raise Cancelled()
    if not total_speech:
        on_part('', 100)
    return '\n\n'.join(texts)


def ffmpeg_path():
    bundled = ROOT / 'tools' / 'ffmpeg.exe'
    if bundled.is_file():
        return str(bundled)
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def validate_fragment(segment):
    if not (math.isfinite(segment.start) and math.isfinite(segment.end)
            and 0 <= segment.start < segment.end and segment.end - segment.start <= 24.001):
        raise ValueError('Для прослушивания или повтора выберите фрагмент с корректными таймкодами до 24 секунд. '
                         'Длинный аудиофайл можно обработать целиком кнопкой «Распознать».')


def convert_audio(source, target, stop, segment=None):
    args = [ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-y', '-protocol_whitelist', 'file,pipe']
    if segment is not None:
        validate_fragment(segment)
        args.extend(['-ss', str(segment.start)])
    args.extend(['-i', str(source)])
    if segment is not None:
        args.extend(['-t', str(segment.end - segment.start)])
    args.extend(['-vn', '-ac', '1', '-ar', str(RATE), '-c:a', 'pcm_s16le', str(target)])
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=errors,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while process.poll() is None:
                if stop.wait(0.1):
                    raise Cancelled()
            if process.returncode:
                errors.seek(0)
                detail = errors.read(4000).decode('utf-8', errors='replace')
                raise ValueError('Не удалось прочитать аудиофайл.\n' + detail)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


class Engine:
    def __init__(self, model_name=MODEL_NAME):
        if model_name not in SUPPORTED_MODELS:
            raise ValueError('Неизвестная модель распознавания.')
        self.model_name = model_name
        self.model = None
        self.vad_model = None

    def load(self):
        assets = ROOT / 'models'
        required = [assets / (self.model_name + '.ckpt'), assets / (self.model_name + '_tokenizer.model')]
        if not all(p.is_file() for p in required):
            if self.model_name == 'v3_e2e_rnnt':
                raise RuntimeError('Альтернативная модель ещё не скачана. Запустите Prepare-alternative.cmd с интернетом.')
            raise RuntimeError('Модель ещё не скачана. Запустите setup.ps1 с интернетом.')
        import torch
        import gigaam
        from silero_vad import load_silero_vad
        torch.set_num_threads(min(4, os.cpu_count() or 2))
        # GigaAM's audio reader calls ffmpeg by its executable name.
        tools = ROOT / 'tools'
        tools.mkdir(exist_ok=True)
        binary = tools / 'ffmpeg.exe'
        if not binary.is_file():
            shutil.copy2(ffmpeg_path(), binary)
        os.environ['PATH'] = str(tools) + os.pathsep + os.environ.get('PATH', '')
        self.model = gigaam.load_model(self.model_name, device='cpu', fp16_encoder=False,
                                      use_flash=False, download_root=str(assets))
        self.vad_model = load_silero_vad()

    def transcribe_fragment(self, source, segment, stop, on_status):
        validate_fragment(segment)
        source = Path(source)
        if not source.is_file():
            raise ValueError('Аудиофайл не найден.')
        if stop.is_set():
            raise Cancelled()
        if self.model is None:
            on_status('Загружаю выбранную модель...')
            self.load()
        with tempfile.TemporaryDirectory(prefix='gigaam-retry-') as directory:
            clip = Path(directory) / 'clip.wav'
            on_status('Подготавливаю выбранный фрагмент...')
            convert_audio(source, clip, stop, segment)
            if stop.is_set():
                raise Cancelled()
            with wave.open(str(clip), 'rb') as audio:
                duration = audio.getnframes() / RATE
                if not duration:
                    raise ValueError('По этим таймкодам в файле нет аудио.')
            on_status('Повторно распознаю фрагмент...')
            result = self.model.transcribe(str(clip))
            text = str(getattr(result, 'text', result)).strip().replace('\u2014', '-').replace('\u2013', '-')
            if stop.is_set():
                raise Cancelled()
            return Segment(segment.start, segment.end, text)

    def transcribe(self, source, stop, on_status, on_part, on_segment=None):
        source = Path(source)
        if not source.is_file():
            raise ValueError('Аудиофайл не найден.')
        if self.model is None or self.vad_model is None:
            on_status('Загружаю модель в память...')
            self.load()
        if stop.is_set():
            raise Cancelled()
        with tempfile.TemporaryDirectory(prefix='gigaam-audio-') as directory:
            wav = Path(directory) / 'audio.wav'
            on_status('Подготавливаю аудио...')
            convert_audio(source, wav, stop)
            on_status('Ищу речь и паузы...')
            ranges = detect_speech_ranges(wav, self.vad_model, stop, on_status)
            on_status('Распознаю на компьютере...')
            return transcribe_wav(wav, self.model, stop, on_part, speech_ranges=ranges, on_segment=on_segment)
