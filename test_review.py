import importlib
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import engine
import transcript
from transcript import Segment, clean_text, render_transcript


class AcknowledgementTests(unittest.TestCase):
    def test_remove_acknowledgements_without_changing_numbers_or_negation(self):
        self.assertEqual(clean_text('УГУ. Ага, не согласен на 15%, а на 20%. Мгм.'),
                         'не согласен на 15%, а на 20%.')

    def test_keep_meaningful_answers_and_words_containing_similar_letters(self):
        source = 'Да, нет, не надо. Кугуев и бумага. Угу, завтра.'
        self.assertEqual(clean_text(source), 'Да, нет, не надо. Кугуев и бумага. завтра.')

    def test_cleanup_can_be_disabled_and_raw_segments_remain_unchanged(self):
        self.assertIn('remove_acknowledgements', clean_text.__code__.co_varnames)
        source = 'Угу. Ага, не надо.'
        self.assertEqual(clean_text(source, remove_acknowledgements=False), source)
        segments = [Segment(3, 4, 'Угу.'), Segment(5, 9, 'Ага, не надо.')]
        self.assertEqual(segments[0].text, 'Угу.')
        self.assertNotIn('00:00:03', render_transcript(segments, cleaned=True))

    def test_no_orphan_timestamp_after_removing_empty_reply(self):
        raw = render_transcript([Segment(3, 4, 'Угу.'), Segment(5, 9, 'Не надо.')])
        self.assertEqual(clean_text(raw), render_transcript([Segment(5, 9, 'Не надо.')]))


class TimedEditingTests(unittest.TestCase):
    def setUp(self):
        self.segments = [Segment(251.5, 254.5, 'Угу.'), Segment(260, 280, 'Не согласен на 15%.')]
        self.raw = render_transcript(self.segments)

    def test_find_fragment_from_text_cursor_and_parse_absolute_times(self):
        self.assertTrue(callable(getattr(transcript, 'segment_at_position', None)))
        selected = transcript.segment_at_position(self.raw, self.raw.index('Не согласен') + 4)
        self.assertEqual(selected, self.segments[1])
        self.assertIsNone(transcript.segment_at_position('Нет таймкодов.', 4))

    def test_replace_only_selected_fragment_preserving_other_manual_edits(self):
        self.assertTrue(callable(getattr(transcript, 'replace_timed_segment', None)))
        edited = self.raw.replace('Не согласен на 15%.', 'Ручная правка: не согласен на 15%.')
        result = transcript.replace_timed_segment(edited, Segment(251.5, 254.5, 'Повторная расшифровка.'))
        self.assertIn('Повторная расшифровка.', result)
        self.assertIn('Ручная правка: не согласен на 15%.', result)
        self.assertNotIn('Угу.', result)

    def test_insert_fragment_previously_omitted_by_cleanup_in_correct_order(self):
        self.assertTrue(callable(getattr(transcript, 'replace_timed_segment', None)))
        doc = render_transcript(self.segments[1:])
        result = transcript.replace_timed_segment(doc, Segment(251.5, 254.5, 'Новая речь.'))
        self.assertLess(result.index('Новая речь.'), result.index('Не согласен'))


class FragmentEngineTests(unittest.TestCase):
    def test_engine_accepts_supported_model_and_rejects_unknown_name(self):
        self.assertIn('model_name', engine.Engine.__init__.__code__.co_varnames)
        self.assertEqual(engine.Engine('v3_e2e_rnnt').model_name, 'v3_e2e_rnnt')
        with self.assertRaises(ValueError):
            engine.Engine('invented')

    def test_fragment_keeps_source_offsets_and_uses_only_local_audio(self):
        self.assertTrue(callable(getattr(engine.Engine, 'transcribe_fragment', None)))
        from test_engine import write_wav
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'audio.wav'
            write_wav(source, [1000] * engine.RATE * 8)
            instance = engine.Engine()
            instance.model = type('Model', (), {'transcribe': lambda self, clip: 'Не надо 15%.'})()
            instance.vad_model = object()
            result = instance.transcribe_fragment(source, Segment(3, 5, 'Старое.'),
                                                 threading.Event(), lambda text: None)
            self.assertEqual(result, Segment(3, 5, 'Не надо 15%.'))

    def test_invalid_or_too_long_range_does_not_load_model(self):
        self.assertTrue(callable(getattr(engine.Engine, 'transcribe_fragment', None)))
        instance = engine.Engine()
        with patch.object(instance, 'load', side_effect=AssertionError('Unexpected model load')):
            for start, end in [(-1, 4), (5, 5), (0, 25), (float('nan'), 1)]:
                with self.assertRaises(ValueError):
                    instance.transcribe_fragment(Path(__file__), Segment(start, end, ''),
                                                 threading.Event(), lambda text: None)


class PreviewTests(unittest.TestCase):
    def test_prepare_local_audio_extracts_exact_range_without_altering_source(self):
        self.assertTrue((engine.ROOT / 'audio_preview.py').is_file())
        preview = importlib.import_module('audio_preview')
        from test_engine import write_wav
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'audio.wav'
            write_wav(source, [1000] * engine.RATE * 8)
            original = source.read_bytes()
            clip = preview.prepare_preview(source, Segment(3, 5, ''), threading.Event())
            try:
                with wave.open(str(clip.path), 'rb') as audio:
                    self.assertEqual(audio.getnframes(), 2 * engine.RATE)
                    self.assertEqual(audio.getframerate(), engine.RATE)
                self.assertEqual(source.read_bytes(), original)
            finally:
                clip.close()
            self.assertFalse(clip.path.exists())

    def test_stop_releases_audio_file_and_play_failure_cleans_up(self):
        self.assertTrue((engine.ROOT / 'audio_preview.py').is_file())
        preview = importlib.import_module('audio_preview')
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'audio.wav'
            from test_engine import write_wav
            write_wav(source, [0] * engine.RATE)
            clip = preview.prepare_preview(source, Segment(0, 1, ''), threading.Event())
            with patch('audio_preview.winsound.PlaySound'):
                player = preview.AudioPlayer()
                player.play(clip)
                player.stop()
            self.assertFalse(clip.path.exists())
            second = preview.prepare_preview(source, Segment(0, 1, ''), threading.Event())
            with patch('audio_preview.winsound.PlaySound', side_effect=[None, RuntimeError('No audio device')]):
                with self.assertRaises(RuntimeError):
                    player.play(second)
            self.assertFalse(second.path.exists())


if __name__ == '__main__':
    unittest.main()
