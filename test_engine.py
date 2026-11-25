import array
import tempfile
import threading
import unittest
import wave
from pathlib import Path

from engine import Cancelled, Engine, iter_chunks, transcribe_wav


def write_wav(path, samples, rate=16000):
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(array.array('h', samples).tobytes())


class AudioTests(unittest.TestCase):
    def test_unreadable_m4a_reports_short_error_before_loading_model(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'unreadable.m4a'
            source.write_bytes(b'\x00\x00\x00\x14ftypisom\x00\x00\x00\x01isom')
            engine = Engine()
            with self.assertRaisesRegex(ValueError, 'Не удалось прочитать аудио') as caught:
                engine.transcribe(source, threading.Event(), lambda *_: None, lambda *_: None)
            self.assertIsNone(engine.model)
            self.assertNotIn('moov atom', str(caught.exception))
            self.assertTrue(any('moov atom' in note for note in caught.exception.__notes__))

    def test_speaker_turns_keep_labels_offsets_and_do_not_repeat_overlapping_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.wav'
            write_wav(path, [1000] * 16000 * 4)
            segments = []
            transcribe_wav(path, None, threading.Event(), lambda *_: None,
                           on_segment=segments.append, decode_pcm=lambda pcm: 'Реплика.',
                           speaker_turns=[(0, 2, 7), (1.5, 3, 3), (3, 4, 7)])
            self.assertEqual([(s.start, s.end, s.speaker) for s in segments],
                             [(0, 2, 1), (2, 3, 2), (3, 4, 1)])

    def test_pcm_decoder_receives_each_speech_fragment_without_temp_wav(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.wav'
            samples = [1000] * 16000 + [-2000] * 16000
            write_wav(path, samples)
            received = []
            segments = []

            def decode(pcm):
                received.append(pcm)
                return 'Готовый фрагмент.'

            transcribe_wav(path, None, threading.Event(), lambda *_: None,
                           speech_ranges=[(16000, 32000)], on_segment=segments.append,
                           decode_pcm=decode)
            self.assertEqual(received, [array.array('h', samples[16000:]).tobytes()])
            self.assertEqual([(item.start, item.end, item.text) for item in segments],
                             [(1, 2, 'Готовый фрагмент.')])

    def test_long_audio_is_preserved_without_gaps_or_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.wav'
            samples = [(i % 2000) - 1000 for i in range(16000 * 61 + 17)]
            write_wav(path, samples)
            chunks = list(iter_chunks(path))
            self.assertEqual(b''.join(c.pcm for c in chunks), array.array('h', samples).tobytes())
            self.assertTrue(all(0 < c.frames <= 24 * 16000 for c in chunks))
            self.assertEqual(chunks[-1].end_frame, len(samples))

    def test_split_prefers_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.wav'
            samples = [2000] * (16000 * 40)
            samples[21 * 16000:22 * 16000] = [0] * 16000
            write_wav(path, samples)
            first = next(iter_chunks(path))
            self.assertGreaterEqual(first.end_frame, 21 * 16000)
            self.assertLess(first.end_frame, 22 * 16000)

    def test_cancel_keeps_completed_text_and_stops_next_chunk(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.wav'
            write_wav(path, [100] * (16000 * 50))
            stop = threading.Event()
            parts = []

            class Model:
                calls = 0
                def transcribe(self, clip):
                    self.calls += 1
                    with wave.open(clip) as f:
                        assert f.getnframes() <= 24 * 16000
                    return type('Result', (), {'text': 'Готовый фрагмент.'})()

            model = Model()
            def on_part(text, progress):
                parts.append(text)
                stop.set()

            with self.assertRaises(Cancelled):
                transcribe_wav(path, model, stop, on_part)
            self.assertEqual(parts, ['Готовый фрагмент.'])
            self.assertEqual(model.calls, 1)

    def test_empty_and_wrong_format_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.wav'
            write_wav(path, [])
            with self.assertRaises(ValueError):
                list(iter_chunks(path))
            write_wav(path, [100] * 1000, rate=8000)
            with self.assertRaises(ValueError):
                list(iter_chunks(path))


if __name__ == '__main__':
    unittest.main()
