"""Explicit one-time download; no downloads during normal recognition."""
from prepare_model import PREFERRED_MODEL, RNNT_HASH, download


if __name__ == '__main__':
    download(PREFERRED_MODEL + '.ckpt', RNNT_HASH)
    download(PREFERRED_MODEL + '_tokenizer.model')
    print('Alternative model ready. Recognition works offline.', flush=True)
