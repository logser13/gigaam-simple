"""Explicit one-time download; no downloads during normal recognition."""
from prepare_model import download


if __name__ == '__main__':
    download('v3_e2e_rnnt.ckpt', '2730de7545ac43ad256485a462b0a27a')
    download('v3_e2e_rnnt_tokenizer.model')
    print('Alternative model ready. Recognition works offline.', flush=True)
