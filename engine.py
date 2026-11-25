"""Local file transcription. No network calls during recognition."""
import array
import math
import os
import subprocess
import sys
import tempfile
import threading
import wave
from contextlib import nullcontext
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from transcript import Segment

ROOT = Path(__file__).resolve().parent
MODEL_NAME = 'v3_e2e_ctc'
PREFERRED_MODEL = 'v3_e2e_rnnt'
SUPPORTED_MODELS = (MODEL_NAME, PREFERRED_MODEL)
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


def iter_speech_chunks(path, ranges, *, packed=False):
    with wave.open(str(path), 'rb') as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, RATE):
            raise ValueError('Требуется WAV: моно, 16 кГц, 16 бит.')
        total = audio.getnframes()
        if not total:
            raise ValueError('В файле нет аудио.')
        for start, end in ranges if packed else pack_speech_ranges(ranges, total):
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


def normalize_result(result):
    return str(getattr(result, 'text', result)).strip().replace('\u2014', '-').replace('\u2013', '-')


def recognize_pcm(model, pcm):
    """Use the pinned GigaAM v3 frontend and decoder on mono 16 kHz PCM."""
    import torch

    with torch.inference_mode():
        samples = torch.frombuffer(bytearray(pcm), dtype=torch.int16).float().div_(32768.0)
        wav = samples.unsqueeze(0)
        length = torch.tensor([samples.numel()])
        encoded, encoded_length = model(wav, length)
        text, _, _ = model.decoding.decode(model.head, encoded, encoded_length)[0]
        return text


def ordered_speaker_ranges(turns, total_frames):
    """Resolve overlaps once and number voices by their first retained turn."""
    ranges = []
    labels = {}
    previous_end = 0
    for start, end, speaker in sorted(turns, key=lambda turn: (turn[0], -turn[1], turn[2])):
        start = max(previous_end, round(start * RATE))
        end = min(total_frames, round(end * RATE))
        if start >= end:
            continue
        label = labels.setdefault(speaker, len(labels) + 1)
        if ranges and ranges[-1][2] == label and start - previous_end <= RATE // 4:
            ranges[-1] = (ranges[-1][0], end, label)
        else:
            ranges.append((start, end, label))
        previous_end = end
    return ranges


def iter_labeled_chunks(path, ranges):
    for start, end, speaker in ranges:
        for chunk in iter_speech_chunks(path, [(start, end)], packed=True):
            yield chunk, speaker


def transcribe_wav(path, model, stop, on_part, speech_ranges=None, on_segment=None, *, decode_pcm=None,
                   speaker_turns=None):
    texts = []
    with wave.open(str(path), 'rb') as audio:
        total_frames = audio.getnframes()
    if speaker_turns is not None:
        ranges = ordered_speaker_ranges(speaker_turns, total_frames)
        iterator = iter_labeled_chunks(path, ranges)
        total_speech = sum(end - start for start, end, _ in ranges)
    elif speech_ranges is not None:
        ranges = pack_speech_ranges(speech_ranges, total_frames)
        iterator = ((chunk, None) for chunk in iter_speech_chunks(path, ranges, packed=True))
        total_speech = sum(end - start for start, end in ranges)
    else:
        iterator = ((chunk, None) for chunk in iter_chunks(path))
        total_speech = total_frames
    processed = 0
    workspace = tempfile.TemporaryDirectory(prefix='gigaam-clips-') if decode_pcm is None else nullcontext()
    with workspace as directory:
        for chunk, speaker in iterator:
            if stop.is_set():
                raise Cancelled()
            if decode_pcm is None:
                clip = Path(directory) / 'clip.wav'
                with wave.open(str(clip), 'wb') as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(RATE)
                    f.writeframes(chunk.pcm)
                result = model.transcribe(str(clip))
            else:
                result = decode_pcm(chunk.pcm)
            text = normalize_result(result)
            if text:
                texts.append(text)
                if on_segment is not None:
                    on_segment(Segment(chunk.start_frame / RATE, chunk.end_frame / RATE, text, speaker))
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
                error = ValueError(
                    'Не удалось прочитать аудиофайл. Проверьте, воспроизводится ли запись целиком. '
                    'Скачайте её заново из исходного источника или экспортируйте рабочую копию в WAV.')
                error.add_note(f'FFmpeg exited with code {process.returncode}.\n{detail}')
                raise error
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def model_available(name):
    assets = ROOT / 'models'
    return all((assets / (name + suffix)).is_file() for suffix in ('.ckpt', '_tokenizer.model'))


class Engine:
    def __init__(self, model_name=MODEL_NAME):
        if model_name not in SUPPORTED_MODELS:
            raise ValueError('Неизвестная модель распознавания.')
        self.model_name = model_name
        self.model = None
        self.vad_model = None
        self.decode_pcm = None

    def load(self):
        assets = ROOT / 'models'
        if not model_available(self.model_name):
            if self.model_name == PREFERRED_MODEL:
                raise RuntimeError('Альтернативная модель ещё не скачана. Запустите Prepare-alternative.cmd с интернетом.')
            raise RuntimeError('Модель ещё не скачана. Запустите setup.ps1 с интернетом.')
        import torch
        import gigaam
        from silero_vad import load_silero_vad
        torch.set_num_threads(min(4, os.cpu_count() or 2))
        self.model = gigaam.load_model(self.model_name, device='cpu', fp16_encoder=False,
                                      use_flash=False, download_root=str(assets))
        self.decode_pcm = partial(recognize_pcm, self.model)
        self.vad_model = load_silero_vad()

    def transcribe_fragment(self, source, segment, stop, on_status):
        validate_fragment(segment)
        source = Path(source)
        if not source.is_file():
            raise ValueError('Аудиофайл не найден.')
        if stop.is_set():
            raise Cancelled()
        with tempfile.TemporaryDirectory(prefix='gigaam-retry-') as directory:
            clip = Path(directory) / 'clip.wav'
            on_status('Подготавливаю выбранный фрагмент...')
            convert_audio(source, clip, stop, segment)
            if stop.is_set():
                raise Cancelled()
            with wave.open(str(clip), 'rb') as audio:
                frames = audio.getnframes()
                if not frames:
                    raise ValueError('По этим таймкодам в файле нет аудио.')
                pcm = audio.readframes(frames)
            if self.model is None:
                on_status('Загружаю выбранную модель...')
                self.load()
            if stop.is_set():
                raise Cancelled()
            on_status('Повторно распознаю фрагмент...')
            result = self.decode_pcm(pcm) if self.decode_pcm is not None else self.model.transcribe(str(clip))
            text = normalize_result(result)
            if stop.is_set():
                raise Cancelled()
            return Segment(segment.start, segment.end, text, segment.speaker)

    def transcribe(self, source, stop, on_status, on_part, on_segment=None, *, speaker_count=None):
        source = Path(source)
        if not source.is_file():
            raise ValueError('Аудиофайл не найден.')
        if stop.is_set():
            raise Cancelled()
        with tempfile.TemporaryDirectory(prefix='gigaam-audio-') as directory:
            wav = Path(directory) / 'audio.wav'
            on_status('Подготавливаю аудио...')
            convert_audio(source, wav, stop)
            if stop.is_set():
                raise Cancelled()
            if self.model is None or self.vad_model is None:
                on_status('Загружаю модель в память...')
                self.load()
            if stop.is_set():
                raise Cancelled()
            ranges = None
            turns = None
            if speaker_count is None:
                on_status('Ищу речь и паузы...')
                ranges = detect_speech_ranges(wav, self.vad_model, stop, on_status)
            else:
                from speakers import detect_speakers
                turns = detect_speakers(wav, speaker_count, stop, on_status)
                if not turns:
                    on_status('Ищу речь и паузы...')
                    ranges = detect_speech_ranges(wav, self.vad_model, stop, on_status)
                    turns = None
            on_status('Распознаю на компьютере...')
            return transcribe_wav(wav, self.model, stop, on_part, speech_ranges=ranges, on_segment=on_segment,
                                  decode_pcm=self.decode_pcm, speaker_turns=turns)
