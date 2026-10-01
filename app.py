import logging
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from engine import Cancelled, Engine, ROOT
from audio_preview import AudioPlayer, prepare_preview
from transcript import (Segment, TIMED_HEADER, clean_text, format_timestamp, render_transcript,
                        replace_timed_segment, save_versions, segment_at_position, timed_blocks)

logging.basicConfig(filename=str(ROOT / 'app.log'), encoding='utf-8', level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')


class App:
    def __init__(self, root):
        self.root = root
        self.engine = Engine()
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.started = 0
        self.path = None
        self.transcript_audio = None
        self.source_name = 'transcript'
        self.imported_text_path = None
        self.raw_text = ''
        self.generated_clean = ''
        self.segments = []
        self.remove_acknowledgements = tk.BooleanVar(value=True)
        self.model_choice = tk.StringVar(value='Обычная')
        self.player = AudioPlayer()
        self.preview_stop = threading.Event()
        self.preview_generation = 0
        self.preview_preparing = False
        self.preview_thread = None
        self.closed = False
        root.title('GigaAM - аудио в текст · версия 3')
        root.geometry('900x720')
        root.minsize(680, 640)
        root.configure(bg='#f4f6fa')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 11))
        style.configure('TFrame', background='#f4f6fa')
        style.configure('TLabel', background='#f4f6fa', foreground='#172033')
        style.configure('TButton', padding=(14, 10))
        style.configure('Accent.TButton', background='#2563eb', foreground='white')
        style.map('Accent.TButton', background=[('active', '#1d4ed8'), ('disabled', '#d9e0ec')],
                  foreground=[('disabled', '#475569')])
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Аудио в текст', font=('Segoe UI', 23, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Русская речь · GigaAM v3 · обработка на этом компьютере').pack(anchor='w', pady=(4, 12))
        row = ttk.Frame(frame)
        row.pack(fill='x')
        self.choose = ttk.Button(row, text='Выбрать аудиофайл', command=self.pick)
        self.choose.pack(side='left')
        self.open_text = ttk.Button(row, text='Открыть TXT', command=self.pick_text)
        self.open_text.pack(side='left', padx=10)
        self.filename = tk.StringVar(value='MP3, WAV, M4A, OGG, FLAC или другой аудиофайл')
        ttk.Label(frame, textvariable=self.filename, wraplength=620).pack(anchor='w', pady=(8, 10))
        actions = ttk.Frame(frame)
        actions.pack(fill='x')
        self.run_button = ttk.Button(actions, text='Распознать', style='Accent.TButton',
                                     command=self.start, state='disabled')
        self.run_button.pack(side='left')
        self.cancel = ttk.Button(actions, text='Остановить', command=self.request_stop, state='disabled')
        self.cancel.pack(side='left', padx=10)
        ttk.Label(actions, text='Модель:').pack(side='left', padx=(8, 6))
        self.model_combo = ttk.Combobox(actions, textvariable=self.model_choice,
                                       values=('Обычная', 'Альтернативная'), state='readonly', width=17)
        self.model_combo.pack(side='left')
        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill='x', pady=(12, 6))
        self.status = tk.StringVar(value='Выберите аудио или откройте готовый TXT для очистки.')
        ttk.Label(frame, textvariable=self.status, wraplength=620).pack(anchor='w', pady=(0, 8))
        footer = ttk.Frame(frame)
        footer.pack(side='bottom', fill='x', pady=(14, 0))
        self.copy_button = ttk.Button(footer, text='Копировать', command=self.copy, state='disabled')
        self.copy_button.pack(side='left')
        self.save_button = ttk.Button(footer, text='Сохранить обе версии', command=self.save, state='disabled')
        self.save_button.pack(side='left', padx=10)
        self.clean_button = ttk.Button(footer, text='Очистить заново', command=self.restore_cleaned, state='disabled')
        self.clean_button.pack(side='left')
        review = ttk.Frame(frame)
        review.pack(fill='x', pady=(0, 8))
        self.play_button = ttk.Button(review, text='Прослушать', command=self.play_selected, state='disabled')
        self.play_button.pack(side='left')
        self.stop_audio_button = ttk.Button(review, text='Стоп звук', command=self.stop_preview, state='disabled')
        self.stop_audio_button.pack(side='left', padx=8)
        self.retry_button = ttk.Button(review, text='Распознать фрагмент', command=self.retry_selected, state='disabled')
        self.retry_button.pack(side='left')
        self.notebook = ttk.Notebook(frame)
        self.notebook.pack(fill='both', expand=True)
        raw_tab = ttk.Frame(self.notebook)
        clean_tab = ttk.Frame(self.notebook)
        self.notebook.add(raw_tab, text='Исходный')
        self.notebook.add(clean_tab, text='Очищенный')
        self.raw_view = ScrolledText(raw_tab, font=('Segoe UI', 12), wrap='word', height=8,
                                    bg='white', fg='#172033', padx=12, pady=12, state='disabled')
        self.raw_view.pack(fill='both', expand=True)
        self.ack_checkbox = ttk.Checkbutton(clean_tab, text='Убирать «угу», «ага» и «мгм»',
                                            variable=self.remove_acknowledgements, command=self.restore_cleaned)
        self.ack_checkbox.pack(anchor='w', padx=8, pady=(6, 0))
        ttk.Label(clean_tab, text='Нажмите на таймкод для прослушивания. Имена и числа проверьте по записи.',
                  wraplength=600).pack(anchor='w', padx=8, pady=(2, 6))
        self.text = ScrolledText(clean_tab, font=('Segoe UI', 12), wrap='word', height=8,
                                bg='white', fg='#172033', padx=12, pady=12, undo=True)
        self.text.pack(fill='both', expand=True)
        self.notebook.select(clean_tab)
        for widget in (self.raw_view, self.text):
            widget.tag_configure('timestamp', foreground='#2563eb', underline=True)
            widget.tag_bind('timestamp', '<ButtonRelease-1>', lambda event, w=widget: self.timestamp_clicked(w, event))
            widget.bind('<ButtonRelease-1>', lambda event, w=widget: self.cursor_changed(w, event), add='+')
            widget.bind('<KeyRelease>', lambda event: self.update_review_controls(), add='+')
        self.notebook.bind('<<NotebookTabChanged>>', lambda event: self.update_review_controls())
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def pick(self):
        path = filedialog.askopenfilename(title='Выберите аудиофайл', filetypes=[
            ('Аудиофайлы', '*.wav *.mp3 *.m4a *.ogg *.opus *.flac *.aac *.wma'), ('Все файлы', '*.*')])
        if path:
            self.choose_audio(Path(path))

    def choose_audio(self, path):
        self.stop_preview()
        self.path = Path(path)
        self.filename.set(str(self.path))
        self.run_button.configure(state='normal')
        if self.imported_text_path is not None:
            self.transcript_audio = self.path.resolve()
            self.status.set('Аудио подключено к открытому TXT. Можно прослушивать его таймкоды.')
        elif self.raw_text.strip() and self.transcript_audio != self.path.resolve():
            self.status.set('Выбрано другое аудио. Для него нажмите «Распознать»; прежний текст пока остаётся в окне.')
        else:
            self.status.set('Файл выбран. Нажмите «Распознать».')
        self.update_review_controls()

    def pick_text(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title='Открыть расшифровку', filetypes=[('Текстовый файл', '*.txt')])
        if not path:
            return
        if self.raw_text.strip() and not messagebox.askyesno('Открыть другой текст',
                'Заменить текущий результат? При необходимости сначала сохраните обе версии.'):
            return
        try:
            self.load_text(Path(path))
        except (OSError, UnicodeError) as exc:
            messagebox.showerror('Не удалось открыть TXT', str(exc))

    def load_text(self, path):
        if self.busy:
            return
        path = Path(path)
        original = path.read_text(encoding='utf-8-sig')
        self.stop_preview()
        self.path = None
        self.transcript_audio = None
        self.imported_text_path = path.resolve()
        self.source_name = path.stem
        self.segments = [segment for _, _, segment in timed_blocks(original)]
        self.filename.set(str(path))
        self.set_raw_text(original)
        self.progress['value'] = 0
        self.finish()
        self.status.set('TXT очищен. Исходный файл сохранён, таймкоды из аудио для него не создаются.')

    def set_raw_text(self, text):
        self.raw_text = text
        self.generated_clean = clean_text(text, self.remove_acknowledgements.get())
        self.write_views(self.generated_clean)

    def write_views(self, cleaned):
        self.raw_view.configure(state='normal')
        self.raw_view.delete('1.0', 'end')
        self.raw_view.insert('1.0', self.raw_text)
        self.raw_view.configure(state='disabled')
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', cleaned)
        for widget in (self.raw_view, self.text):
            for match in TIMED_HEADER.finditer(widget.get('1.0', 'end')):
                widget.tag_add('timestamp', f'1.0+{match.start()}c', f'1.0+{match.end()}c')
        if self.busy:
            self.raw_view.see('end')
            self.text.see('end')
            self.text.configure(state='disabled')
        self.update_review_controls()

    def restore_cleaned(self):
        if self.busy or not self.raw_text.strip():
            return
        cleaned = clean_text(self.raw_text, self.remove_acknowledgements.get())
        if self.text.get('1.0', 'end').strip() != self.generated_clean.strip() and not messagebox.askyesno(
                'Повторить очистку', 'Заменить ручные правки новой очисткой исходного текста?'):
            return
        self.generated_clean = cleaned
        self.write_views(cleaned)
        self.status.set('Очистка выполнена. Исходная расшифровка осталась во вкладке «Исходный».')

    def start(self):
        if self.busy or not self.path:
            return
        if self.raw_text.strip() and not messagebox.askyesno(
                'Новая запись', 'Заменить текущий текст результатом новой записи?'):
            return
        self.stop_preview()
        self.select_engine()
        self.busy = True
        self.started = time.monotonic()
        self.imported_text_path = None
        self.transcript_audio = self.path.resolve()
        self.source_name = self.path.stem
        self.segments = []
        self.stop.clear()
        self.set_raw_text('')
        self.progress['value'] = 0
        self.progress.configure(mode='indeterminate')
        self.progress.start(15)
        for button in (self.choose, self.open_text, self.run_button, self.copy_button, self.save_button, self.clean_button):
            button.configure(state='disabled')
        self.model_combo.configure(state='disabled')
        self.ack_checkbox.configure(state='disabled')
        self.update_review_controls()
        self.cancel.configure(state='normal')
        self.status.set('Запускаю...')
        threading.Thread(target=self.work, args=(self.path,), daemon=True).start()

    def work(self, path):
        try:
            self.engine.transcribe(path, self.stop,
                lambda s: self.events.put(('status', s)),
                lambda text, p: self.events.put(('part', (text, p))),
                on_segment=lambda segment: self.events.put(('segment', segment)))
            self.events.put(('done', None))
        except Cancelled:
            self.events.put(('cancelled', None))
        except Exception as exc:
            logging.exception('Transcription failed')
            self.events.put(('error', str(exc)))

    def request_stop(self):
        self.stop.set()
        self.cancel.configure(state='disabled')
        self.status.set('Останавливаю... Текущий фрагмент завершится, готовый текст останется.')

    def select_engine(self):
        name = 'v3_e2e_rnnt' if self.model_choice.get() == 'Альтернативная' else 'v3_e2e_ctc'
        if self.engine.model_name != name:
            self.engine = Engine(name)

    def selected_segment(self):
        widget = self.raw_view if self.notebook.index('current') == 0 else self.text
        count = widget.count('1.0', 'insert', 'chars')
        position = int(count[0]) if count else 0
        return segment_at_position(widget.get('1.0', 'end'), position)

    def review_audio(self):
        if self.path and self.path.is_file() and self.transcript_audio == self.path.resolve():
            return self.path
        return None

    def cursor_changed(self, widget, event):
        widget.mark_set('insert', widget.index(f'@{event.x},{event.y}'))
        self.update_review_controls()

    def timestamp_clicked(self, widget, event):
        self.cursor_changed(widget, event)
        self.play_selected()

    def update_review_controls(self):
        if not hasattr(self, 'retry_button') or not hasattr(self, 'text'):
            return
        available = not self.busy and self.review_audio() is not None and self.selected_segment() is not None
        self.play_button.configure(state='normal' if available and not self.preview_preparing else 'disabled')
        self.retry_button.configure(state='normal' if available else 'disabled')
        self.stop_audio_button.configure(state='normal' if self.preview_preparing or self.player.clip is not None else 'disabled')

    def stop_preview(self):
        was_active = self.preview_preparing or self.player.clip is not None
        self.preview_generation += 1
        self.preview_stop.set()
        self.preview_preparing = False
        self.player.stop()
        self.update_review_controls()
        if was_active and not self.busy and not self.closed:
            self.status.set('Прослушивание остановлено.')

    def play_selected(self):
        if self.busy or self.review_audio() is None:
            return
        segment = self.selected_segment()
        if segment is None:
            return
        self.stop_preview()
        generation = self.preview_generation
        stop = self.preview_stop = threading.Event()
        self.preview_preparing = True
        self.update_review_controls()
        self.status.set('Подготавливаю звук выбранного фрагмента...')
        self.preview_thread = threading.Thread(target=self.prepare_playback,
            args=(self.path, segment, stop, generation), daemon=True)
        self.preview_thread.start()

    def prepare_playback(self, source, segment, stop, generation):
        try:
            clip = prepare_preview(source, segment, stop)
            self.events.put(('preview_ready', (generation, clip)))
        except Cancelled:
            self.events.put(('preview_cancelled', generation))
        except Exception as exc:
            self.events.put(('preview_error', (generation, str(exc))))

    def finish_preview(self, generation):
        if generation == self.preview_generation and not self.closed:
            self.stop_preview()
            self.status.set('Фрагмент прослушан.')

    def retry_selected(self):
        if self.busy or self.review_audio() is None:
            return
        segment = self.selected_segment()
        if segment is None:
            return
        self.stop_preview()
        self.select_engine()
        self.busy = True
        self.started = time.monotonic()
        self.stop.clear()
        self.progress.configure(mode='indeterminate')
        self.progress.start(15)
        self.text.configure(state='disabled')
        for button in (self.choose, self.open_text, self.run_button, self.copy_button, self.save_button, self.clean_button):
            button.configure(state='disabled')
        self.model_combo.configure(state='disabled')
        self.ack_checkbox.configure(state='disabled')
        self.cancel.configure(state='normal')
        self.update_review_controls()
        self.status.set('Повторно распознаю выбранный фрагмент...')
        threading.Thread(target=self.work_fragment, args=(self.path, segment), daemon=True).start()

    def work_fragment(self, source, segment):
        try:
            result = self.engine.transcribe_fragment(source, segment, self.stop,
                lambda text: self.events.put(('status', text)))
            self.events.put(('fragment_done', result))
        except Cancelled:
            self.events.put(('cancelled', None))
        except Exception as exc:
            logging.exception('Fragment recognition failed')
            self.events.put(('error', str(exc)))

    def apply_fragment_result(self, segment):
        if not any(character.isalnum() for character in segment.text):
            return False
        cleaned = self.text.get('1.0', 'end').strip()
        self.raw_text = replace_timed_segment(self.raw_text, segment)
        clean_segment = Segment(segment.start, segment.end, clean_text(segment.text, self.remove_acknowledgements.get()))
        cleaned = replace_timed_segment(cleaned, clean_segment)
        self.segments = [item for _, _, item in timed_blocks(self.raw_text)]
        self.generated_clean = clean_text(self.raw_text, self.remove_acknowledgements.get())
        self.write_views(cleaned)
        for widget in (self.raw_view, self.text):
            for match, _, item in timed_blocks(widget.get('1.0', 'end')):
                if (format_timestamp(item.start), format_timestamp(item.end)) == (
                        format_timestamp(segment.start), format_timestamp(segment.end)):
                    position = f'1.0+{match.end() + 1}c'
                    widget.mark_set('insert', position)
                    widget.see(position)
                    break
        return True

    def finish(self):
        self.busy = False
        self.progress.stop()
        self.progress.configure(mode='determinate')
        self.text.configure(state='normal')
        self.choose.configure(state='normal')
        self.open_text.configure(state='normal')
        self.run_button.configure(state='normal' if self.path else 'disabled')
        self.cancel.configure(state='disabled')
        has_text = bool(self.raw_text.strip())
        self.copy_button.configure(state='normal' if has_text else 'disabled')
        self.save_button.configure(state='normal' if has_text else 'disabled')
        self.clean_button.configure(state='normal' if has_text else 'disabled')
        self.model_combo.configure(state='readonly')
        self.ack_checkbox.configure(state='normal')
        self.update_review_controls()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'status' and not self.stop.is_set():
                    self.status.set(value)
                elif kind == 'segment':
                    self.segments.append(value)
                    self.set_raw_text(render_transcript(self.segments))
                elif kind == 'part':
                    text, percent = value
                    self.progress.stop()
                    self.progress.configure(mode='determinate', value=percent)
                    if not self.stop.is_set():
                        self.status.set(f'Распознано {percent:.0f}% · прошло {time.monotonic() - self.started:.0f} с')
                elif kind == 'done':
                    self.finish()
                    self.progress['value'] = 100
                    if self.raw_text.strip():
                        self.status.set(f'Готово за {time.monotonic() - self.started:.1f} с. Проверьте очищенный текст и сохраните обе версии.')
                    else:
                        self.status.set('Речь не распознана. Попробуйте запись с более отчётливым голосом.')
                elif kind == 'fragment_done':
                    changed = self.apply_fragment_result(value)
                    self.finish()
                    self.progress['value'] = 100
                    self.status.set('Фрагмент обновлён. Правки в остальных фрагментах сохранены.' if changed else
                                    'Речь во фрагменте не распознана. Предыдущий текст сохранён.')
                elif kind == 'preview_ready':
                    generation, clip = value
                    if generation != self.preview_generation or self.closed or self.busy:
                        clip.close()
                        continue
                    self.preview_preparing = False
                    try:
                        self.player.play(clip)
                        import wave
                        with wave.open(str(clip.path), 'rb') as audio:
                            milliseconds = int(audio.getnframes() / audio.getframerate() * 1000)
                        self.root.after(milliseconds + 200, lambda g=generation: self.finish_preview(g))
                        self.status.set('Прослушивание выбранного фрагмента. Нажмите «Стоп звук» для остановки.')
                    except Exception as exc:
                        self.status.set('Не удалось воспроизвести звук.')
                        messagebox.showerror('Ошибка воспроизведения', str(exc))
                    self.update_review_controls()
                elif kind == 'preview_error':
                    generation, detail = value
                    if generation == self.preview_generation:
                        self.preview_preparing = False
                        self.update_review_controls()
                        self.status.set('Не удалось подготовить звук.')
                        messagebox.showerror('Ошибка воспроизведения', detail)
                elif kind == 'preview_cancelled' and value == self.preview_generation:
                    self.preview_preparing = False
                    self.update_review_controls()
                elif kind == 'cancelled':
                    self.finish()
                    self.status.set('Остановлено. Готовые фрагменты текста сохранены в окне.')
                elif kind == 'error':
                    self.finish()
                    self.status.set('Не удалось распознать файл. Подробности записаны в app.log.')
                    messagebox.showerror('Ошибка', value)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def copy(self):
        self.root.clipboard_clear()
        selected = self.raw_text if self.notebook.index('current') == 0 else self.text.get('1.0', 'end').strip()
        self.root.clipboard_append(selected)
        self.status.set('Текст текущей вкладки скопирован.')

    def save(self):
        target = filedialog.asksaveasfilename(title='Сохранить очищенный текст и исходную копию', defaultextension='.txt',
            initialfile=self.source_name + '.cleaned.txt',
            filetypes=[('Текстовый файл', '*.txt')])
        if target:
            try:
                if self.imported_text_path and Path(target).resolve() == self.imported_text_path:
                    raise ValueError('Выберите другое имя. Исходный TXT не перезаписывается.')
                raw_path, clean_path = save_versions(target, self.raw_text, self.text.get('1.0', 'end').strip())
                self.status.set(f'Сохранено: {clean_path.name} и {raw_path.name}')
            except (OSError, ValueError) as exc:
                messagebox.showerror('Не удалось сохранить', str(exc))

    def close(self):
        if self.busy:
            messagebox.showinfo('Обработка идёт', 'Нажмите «Остановить» и дождитесь завершения текущего фрагмента перед закрытием.')
            return
        self.closed = True
        self.stop_preview()
        self.root.withdraw()
        self.finish_closing()

    def finish_closing(self):
        if self.preview_thread is not None:
            self.preview_thread.join(timeout=0.2)
            if self.preview_thread.is_alive():
                self.root.after(50, self.finish_closing)
                return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'preview_ready':
                    value[1].close()
        except queue.Empty:
            pass
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
