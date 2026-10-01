import array
import tempfile
import threading
import unittest
import wave
from pathlib import Path

from engine import RATE, iter_speech_chunks, pack_speech_ranges, transcribe_wav
from transcript import Segment, clean_text, render_transcript, save_versions
from test_engine import write_wav


class SpeechSegmentationTests(unittest.TestCase):
    def test_skip_silence_keep_original_offsets_and_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.wav'
            samples = [(i % 2000) - 1000 for i in range(RATE * 40)]
            write_wav(path, samples)
            chunks = list(iter_speech_chunks(path, [(RATE * 5, RATE * 9), (RATE * 20, RATE * 23)]))
            self.assertEqual([c.start_frame / RATE for c in chunks], [5, 20])
            self.assertEqual([c.end_frame / RATE for c in chunks], [9, 23])
            self.assertEqual(b''.join(c.pcm for c in chunks),
                array.array('h', samples[RATE * 5:RATE * 9] + samples[RATE * 20:RATE * 23]).tobytes())

    def test_group_nearby_speech_without_duplicate_samples(self):
        ranges = [(0, 10 * RATE), (9 * RATE, 12 * RATE), (13 * RATE, 19 * RATE), (22 * RATE, 30 * RATE)]
        self.assertEqual(pack_speech_ranges(ranges, 35 * RATE), [(0, 19 * RATE), (22 * RATE, 30 * RATE)])

    def test_long_continuous_speech_stays_within_model_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.wav'
            samples = [1000] * (RATE * 61)
            write_wav(path, samples)
            chunks = list(iter_speech_chunks(path, [(0, len(samples))]))
            self.assertTrue(all(c.frames <= 24 * RATE for c in chunks))
            self.assertEqual(sum(c.frames for c in chunks), len(samples))

    def test_timestamps_are_in_source_timeline_not_trimmed_timeline(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.wav'
            write_wav(path, [1000] * RATE * 30)
            model = type('Model', (), {'transcribe': lambda self, clip: 'Речь.'})()
            segments, progress = [], []
            transcribe_wav(path, model, threading.Event(), lambda t, p: progress.append(p),
                           speech_ranges=[(5 * RATE, 8 * RATE), (20 * RATE, 22 * RATE)],
                           on_segment=segments.append)
            self.assertEqual([(s.start, s.end) for s in segments], [(5, 8), (20, 22)])
            self.assertEqual(progress[-1], 100)

    def test_silence_never_sent_to_asr(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.wav'
            write_wav(path, [0] * RATE * 5)
            class Model:
                def transcribe(self, clip):
                    raise AssertionError('Silence should be skipped')
            self.assertEqual(transcribe_wav(path, Model(), threading.Event(), lambda t, p: None,
                                           speech_ranges=[]), '')


class CleanupTests(unittest.TestCase):
    def test_remove_only_explicit_fillers_and_pronoun_stutters(self):
        self.assertEqual(clean_text('Э-э, я-я не хочу скидку 15%. А-а, мы мы обсудим это.'),
                         'я не хочу скидку 15%. мы обсудим это.')

    def test_numbers_names_negations_and_emphasis_survive(self):
        source = 'Иван, не-не, не потому. 40 тысяч клиентов, 2,5%, 2027 год. Очень очень важно. Да, да.'
        self.assertEqual(clean_text(source), source)

    def test_do_not_guess_damaged_numbers_and_names(self):
        source = 'В о4ка регионах. П..т.тнадца процетов. Петрров и ExampleApp.'
        self.assertEqual(clean_text(source), source)

    def test_cleanup_is_idempotent(self):
        source = 'А-а-а, я-я, э-э, мы мы поговорим.\n\nНе надо 15% менять.'
        self.assertEqual(clean_text(clean_text(source)), clean_text(source))

    def test_clean_view_never_mutates_source_segments(self):
        segments = [Segment(251.2, 269.6, 'Э-э, я-я не согласен.')]
        original = render_transcript(segments, timestamps=True)
        cleaned = render_transcript(segments, timestamps=True, cleaned=True)
        self.assertIn('[00:04:11.200 - 00:04:29.600]', original)
        self.assertIn('Э-э, я-я не согласен.', original)
        self.assertNotIn('Э-э', cleaned)
        self.assertEqual(segments[0].text, 'Э-э, я-я не согласен.')

    def test_save_both_versions_and_do_not_overwrite_source_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'meeting.txt'
            raw_path, clean_path = save_versions(path, 'Исходный текст.', 'Очищенный текст.')
            self.assertEqual(raw_path.read_text(encoding='utf-8-sig'), 'Исходный текст.\n')
            self.assertEqual(clean_path.read_text(encoding='utf-8-sig'), 'Очищенный текст.\n')
            # A repeated save creates a new source snapshot rather than replacing it.
            next_raw, _ = save_versions(path, 'Другой исходный.', 'Новый очищенный.')
            self.assertNotEqual(next_raw, raw_path)
            self.assertEqual(raw_path.read_text(encoding='utf-8-sig'), 'Исходный текст.\n')


if __name__ == '__main__':
    unittest.main()
