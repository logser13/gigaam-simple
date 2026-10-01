import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from app import App
from transcript import Segment


class AppTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)

    def tearDown(self):
        self.root.destroy()

    def test_raw_snapshot_survives_cleaned_edits_and_save(self):
        self.app.started = time.monotonic()
        self.app.events.put(('segment', Segment(251.2, 269.6, 'Э-э, я-я не согласен на 15%.')))
        self.app.events.put(('done', None))
        self.app.poll()
        raw = self.app.raw_text
        self.assertIn('Э-э, я-я', raw)
        self.assertIn('[00:04:11.200 - 00:04:29.600]', raw)
        self.assertNotIn('Э-э', self.app.text.get('1.0', 'end'))
        self.assertEqual(str(self.app.raw_view['state']), 'disabled')
        self.app.text.delete('1.0', 'end')
        self.app.text.insert('1.0', 'Ручная правка.')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'meeting.txt'
            with patch('app.filedialog.asksaveasfilename', return_value=str(target)):
                self.app.save()
            self.assertEqual(target.read_text(encoding='utf-8-sig').strip(), 'Ручная правка.')
            self.assertEqual(target.with_name('meeting.original.txt').read_text(encoding='utf-8-sig').strip(), raw)
        self.assertEqual(self.app.raw_text, raw)

    def test_load_old_txt_without_touching_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.txt'
            original = 'Э-э, я-я не меняю 40 тысяч и 2,5%.\n'
            source.write_text(original, encoding='utf-8-sig')
            self.app.load_text(source)
            self.assertEqual(self.app.raw_text, original)
            self.assertNotIn('Э-э', self.app.text.get('1.0', 'end'))
            self.assertIn('не меняю 40 тысяч и 2,5%', self.app.text.get('1.0', 'end'))
            self.assertEqual(source.read_text(encoding='utf-8-sig'), original)
            self.assertEqual(str(self.app.run_button['state']), 'disabled')

    def test_imported_source_file_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.txt'
            source.write_text('Исходный текст.', encoding='utf-8')
            self.app.load_text(source)
            with patch('app.filedialog.asksaveasfilename', return_value=str(source)), patch('app.messagebox.showerror') as error:
                self.app.save()
            error.assert_called_once()
            self.assertEqual(source.read_text(encoding='utf-8'), 'Исходный текст.')


if __name__ == '__main__':
    unittest.main()
