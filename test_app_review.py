import tkinter as tk
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import App
from transcript import Segment, render_transcript


class ReviewAppTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.set_raw_text(render_transcript([
            Segment(5, 8, 'Угу. Не согласен на 15%.'), Segment(20, 23, 'Завтра 40 клиентов.')]))
        self.app.path = Path(__file__)

    def tearDown(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def test_selected_fragment_uses_cursor_in_current_tab(self):
        self.assertTrue(callable(getattr(self.app, 'selected_segment', None)))
        self.app.text.mark_set('insert', '5.3')
        self.assertEqual(self.app.selected_segment().start, 20)
        self.app.notebook.select(0)
        self.app.raw_view.mark_set('insert', '2.2')
        self.assertEqual(self.app.selected_segment().start, 5)

    def test_retranscription_preserves_manual_edits_elsewhere(self):
        self.assertTrue(callable(getattr(self.app, 'apply_fragment_result', None)))
        text = self.app.text.get('1.0', 'end').strip().replace('Завтра 40 клиентов.', 'Ручная правка: завтра 40 клиентов.')
        self.app.text.delete('1.0', 'end')
        self.app.text.insert('1.0', text)
        self.app.apply_fragment_result(Segment(5, 8, 'Ага, не согласен на 20%.'))
        self.assertIn('Ага, не согласен на 20%.', self.app.raw_text)
        cleaned = self.app.text.get('1.0', 'end')
        self.assertNotIn('Ага', cleaned)
        self.assertIn('не согласен на 20%.', cleaned)
        self.assertIn('Ручная правка: завтра 40 клиентов.', cleaned)

    def test_acknowledgement_switch_restores_words_without_unnecessary_prompt(self):
        self.assertTrue(hasattr(self.app, 'remove_acknowledgements'))
        self.app.remove_acknowledgements.set(False)
        with patch('app.messagebox.askyesno') as question:
            self.app.restore_cleaned()
        self.assertIn('Угу.', self.app.text.get('1.0', 'end'))
        question.assert_not_called()

    def test_stale_preview_is_discarded_and_never_played(self):
        self.assertTrue(hasattr(self.app, 'preview_generation'))
        clip = Mock()
        with patch.object(self.app.player, 'play') as play:
            self.app.events.put(('preview_ready', (self.app.preview_generation - 1, clip)))
            self.app.poll()
            play.assert_not_called()
        clip.close.assert_called_once()

    def test_playback_and_retry_disabled_without_attached_audio(self):
        self.assertTrue(hasattr(self.app, 'play_button'))
        self.app.path = None
        self.app.finish()
        self.assertEqual(str(self.app.play_button['state']), 'disabled')
        self.assertEqual(str(self.app.retry_button['state']), 'disabled')

    def test_model_change_selects_alternative_without_loading_in_ui_thread(self):
        self.assertTrue(callable(getattr(self.app, 'select_engine', None)))
        self.app.model_choice.set('Альтернативная')
        self.app.select_engine()
        self.assertEqual(self.app.engine.model_name, 'v3_e2e_rnnt')
        self.assertIsNone(self.app.engine.model)

    def test_empty_retry_preserves_previous_text_and_manual_corrections(self):
        self.assertTrue(callable(getattr(self.app, 'apply_fragment_result', None)))
        raw = self.app.raw_text
        self.app.text.insert('end', '\nМоя правка.')
        clean = self.app.text.get('1.0', 'end')
        for empty in ('', ' . '):
            self.app.apply_fragment_result(Segment(5, 8, empty))
            self.assertEqual(self.app.raw_text, raw)
            self.assertEqual(self.app.text.get('1.0', 'end'), clean)

    def test_cursor_at_document_start_and_empty_document_do_not_crash(self):
        self.assertTrue(callable(getattr(self.app, 'selected_segment', None)))
        self.app.text.mark_set('insert', '1.0')
        self.assertEqual(self.app.selected_segment().start, 5)
        self.app.set_raw_text('')
        self.assertIsNone(self.app.selected_segment())

    def test_new_audio_cannot_retranscribe_text_from_previous_recording(self):
        self.assertTrue(callable(getattr(self.app, 'choose_audio', None)))
        audio_a = Path(__file__).resolve()
        self.app.path = audio_a
        self.app.transcript_audio = audio_a
        with patch('app.filedialog.askopenfilename', return_value=str(Path(__file__).with_name('test_app.py'))):
            self.app.pick()
        self.assertEqual(str(self.app.retry_button['state']), 'disabled')
        self.assertEqual(str(self.app.play_button['state']), 'disabled')
        self.assertEqual(self.app.transcript_audio, audio_a)

    def test_imported_timed_txt_can_explicitly_attach_its_audio(self):
        self.assertTrue(callable(getattr(self.app, 'choose_audio', None)))
        self.app.imported_text_path = Path('meeting.txt')
        self.app.choose_audio(Path(__file__))
        self.assertEqual(self.app.transcript_audio, Path(__file__).resolve())
        self.assertEqual(str(self.app.play_button['state']), 'normal')

    def test_cursor_stays_at_retranscribed_fragment(self):
        self.app.text.mark_set('insert', '2.4')
        self.app.apply_fragment_result(Segment(5, 8, 'Новый текст без 15%.'))
        self.assertEqual(self.app.selected_segment().start, 5)

    def test_close_waits_for_preview_worker_and_cleans_queued_clip(self):
        self.assertTrue(hasattr(self.app, 'preview_thread'))
        clip = Mock()
        def worker():
            self.app.preview_stop.wait(3)
            self.app.events.put(('preview_ready', (self.app.preview_generation - 1, clip)))
        self.app.preview_thread = threading.Thread(target=worker)
        self.app.preview_thread.start()
        self.app.close()
        self.assertFalse(self.app.preview_thread.is_alive())
        clip.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
