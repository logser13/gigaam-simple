"""Local audio previews using the Windows audio API and bounded WAV excerpts."""
import tempfile
import winsound
from dataclasses import dataclass
from pathlib import Path

from engine import convert_audio, validate_fragment


@dataclass
class PreviewClip:
    path: Path
    directory: tempfile.TemporaryDirectory

    def close(self):
        self.directory.cleanup()


def prepare_preview(source, segment, stop):
    validate_fragment(segment)
    directory = tempfile.TemporaryDirectory(prefix='gigaam-preview-')
    clip = PreviewClip(Path(directory.name) / 'preview.wav', directory)
    try:
        convert_audio(source, clip.path, stop, segment)
        return clip
    except BaseException:
        clip.close()
        raise


class AudioPlayer:
    def __init__(self):
        self.clip = None

    def play(self, clip):
        try:
            self.stop()
            winsound.PlaySound(str(clip.path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.clip = clip
        except Exception:
            clip.close()
            raise

    def stop(self):
        winsound.PlaySound(None, 0)
        if self.clip is not None:
            self.clip.close()
            self.clip = None
