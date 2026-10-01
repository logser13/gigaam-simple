"""Small smoke check of the actual Tk interface, without loading the model."""
import time
import tkinter as tk
from app import App
from transcript import Segment

root = tk.Tk()
app = App(root)
root.update()
assert root.winfo_rooty() + root.winfo_height() <= root.winfo_screenheight(), 'Default window extends below the screen'
assert str(app.run_button['state']) == 'disabled'
app.started = time.monotonic()
app.events.put(('segment', Segment(5, 8, 'Э-э, проверка интерфейса.')))
app.events.put(('part', ('Э-э, проверка интерфейса.', 100)))
app.events.put(('done', None))
app.poll()
root.update()
assert 'Э-э' in app.raw_text
assert 'Э-э' not in app.text.get('1.0', 'end')
assert '[00:00:05.000 - 00:00:08.000]' in app.raw_text
assert str(app.save_button['state']) == 'normal'
assert str(app.cancel['state']) == 'disabled'
assert not app.busy
assert root.winfo_width() >= 680
assert root.winfo_height() >= 560
controls = (app.choose, app.open_text, app.run_button, app.copy_button, app.save_button,
            app.clean_button, app.play_button, app.stop_audio_button, app.retry_button,
            app.model_combo, app.ack_checkbox)
for button in controls:
    assert button.winfo_ismapped(), 'A required button is hidden'
    assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
root.geometry('680x560')
root.update()
for button in controls:
    assert button.winfo_ismapped(), 'A required button is hidden at minimum size'
    assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width(), 'Control overflows horizontally'
assert app.text.winfo_height() >= 80, 'Cleaned text is too small to read at minimum size'
root.destroy()
print('UI smoke: passed')
