"""Conservative, deterministic cleanup and separate source snapshots."""
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


# Only explicit hesitation sounds and a small list of grammatical stutters.
# Never infer damaged numbers, names, abbreviations or missing negations.
FILLERS = re.compile(r'(?<!\w)(?:э(?:-э)+|а(?:-а)+|м(?:-м)+|э{2,}|м{3,}|хм{2,})(?!\w)[,;:]?[ \t]*', re.I)
STUTTERS = r'я|мы|вы|он|она|оно|они|ну|там|вот|это|и|или'
HYPHEN_REPEAT = re.compile(rf'\b({STUTTERS})[ \t]*-[ \t]*\1\b', re.I)
WORD_REPEAT = re.compile(rf'\b({STUTTERS})[ \t]+\1\b', re.I)
ACKNOWLEDGEMENTS = re.compile(r'(?<!\w)(?:угу|у-гу|ага|а-га|мгм|м-г-м)(?!\w)[,;:.!?]?[ \t]*', re.I)
STAMP = r'\d{2,}:[0-5]\d:[0-5]\d\.\d{3}'
TIMED_HEADER = re.compile(rf'^\[({STAMP}) - ({STAMP})\][ \t]*\r?$', re.M)


def clean_text(text, remove_acknowledgements=True):
    text = text.replace('\u2014', '-').replace('\u2013', '-')
    text = FILLERS.sub('', text)
    if remove_acknowledgements:
        text = ACKNOWLEDGEMENTS.sub('', text)
    while True:
        changed = WORD_REPEAT.sub(r'\1', HYPHEN_REPEAT.sub(r'\1', text))
        if changed == text:
            break
        text = changed
    lines = []
    for line in text.splitlines():
        line = re.sub(r'[ \t]+', ' ', line).strip()
        line = re.sub(r' +([,.;:!?])', r'\1', line)
        # Pure punctuation emitted on noise is omitted only from the clean version.
        if line and not any(c.isalnum() for c in line):
            line = ''
        if line or (lines and lines[-1]):
            lines.append(line)
    cleaned = '\n'.join(lines).strip()
    # An acknowledgement-only fragment has no content in the cleaned view.
    for match, end, segment in reversed(list(timed_blocks(cleaned))):
        if not segment.text.strip():
            cleaned = cleaned[:match.start()] + cleaned[end:]
    return re.sub(r'\n{3,}', '\n\n', cleaned).strip()


def format_timestamp(seconds):
    milliseconds = max(0, round(seconds * 1000))
    total_seconds, ms = divmod(milliseconds, 1000)
    minutes, second = divmod(total_seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f'{hour:02}:{minute:02}:{second:02}.{ms:03}'


def render_transcript(segments, timestamps=True, cleaned=False, remove_acknowledgements=True):
    paragraphs = []
    for segment in segments:
        text = clean_text(segment.text, remove_acknowledgements) if cleaned else segment.text
        if not text.strip():
            continue
        prefix = f'[{format_timestamp(segment.start)} - {format_timestamp(segment.end)}]\n' if timestamps else ''
        paragraphs.append(prefix + text)
    return '\n\n'.join(paragraphs)


def timestamp_seconds(value):
    hours, minutes, seconds = value.split(':')
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def timed_blocks(document):
    headers = list(TIMED_HEADER.finditer(document))
    for index, match in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(document)
        start_seconds, end_seconds = map(timestamp_seconds, match.groups())
        if end_seconds > start_seconds:
            yield match, end, Segment(start_seconds, end_seconds, document[match.end():end].strip())


def segment_at_position(document, position):
    selected = None
    for match, _, segment in timed_blocks(document):
        if match.start() > position:
            break
        selected = segment
    return selected


def replace_timed_segment(document, segment):
    """Replace one timed block, preserving edits in all other blocks."""
    replacement = render_transcript([segment])
    position = len(document)
    stop = position
    for match, end, existing in timed_blocks(document):
        if (format_timestamp(existing.start), format_timestamp(existing.end)) == (
                format_timestamp(segment.start), format_timestamp(segment.end)):
            position, stop = match.start(), end
            break
        if existing.start > segment.start:
            position = stop = match.start()
            break
    return '\n\n'.join(part.strip() for part in (document[:position], replacement, document[stop:]) if part.strip())


def save_versions(target, raw_text, cleaned_text):
    """Write a new immutable source snapshot and the explicitly chosen clean file."""
    target = Path(target)
    number = 1
    while True:
        marker = '.original' if number == 1 else f'.original-{number}'
        raw_path = target.with_name(target.stem + marker + target.suffix)
        try:
            with raw_path.open('x', encoding='utf-8-sig') as source:
                source.write(raw_text.rstrip('\r\n') + '\n')
            break
        except FileExistsError:
            number += 1
    target.write_text(cleaned_text.rstrip('\r\n') + '\n', encoding='utf-8-sig')
    return raw_path, target
