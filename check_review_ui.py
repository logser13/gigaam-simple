"""Exercise the real async audio-preview path with silent local WAV audio."""
import tempfile
import time
import tkinter as tk
from pathlib import Path

from app import App
from engine import RATE
from test_engine import write_wav
from transcript import Segment, render_transcript


with tempfile.TemporaryDirectory() as folder:
    source = Path(folder) / 'silence.wav'
    text = Path(folder) / 'timed.txt'
    write_wav(source, [0] * RATE)
    text.write_text(render_transcript([Segment(0.1, 0.3, 'Угу. Проверка.')]), encoding='utf-8')
    root = tk.Tk()
    root.withdraw()
    app = App(root)
    try:
        app.load_text(text)
        app.choose_audio(source)
        app.text.mark_set('insert', '2.4')
        app.play_selected()
        deadline = time.monotonic() + 8
        was_playing = False
        clip_path = None
        while time.monotonic() < deadline:
            root.update()
            if app.player.clip is not None:
                was_playing = True
                clip_path = app.player.clip.path
            if was_playing and app.player.clip is None:
                break
            time.sleep(0.01)
        assert was_playing, 'Preview was not delivered to the Windows player'
        assert app.player.clip is None, 'Finished preview was not released'
        assert not clip_path.exists(), 'Temporary preview remained after playback'
        assert app.status.get() == 'Фрагмент прослушан.'
        assert 'Угу' in app.raw_text and 'Угу' not in app.text.get('1.0', 'end')
        print('Async UI preview, automatic stop, temporary-file cleanup: passed')
    finally:
        app.close()
