"""Resolve host-owned hero motifs independently of the service working directory."""
import hashlib
import json
import os
from pathlib import Path


def ticker_motifs_path():
    override=os.environ.get('SMN_TICKER_MOTIFS_FILE')
    if override:
        path=Path(override)
        if not path.is_absolute():
            raise ValueError('SMN_TICKER_MOTIFS_FILE must be an absolute path')
        return path
    local=Path(__file__).resolve().with_name('ticker_motif_custom.json')
    return local if local.is_file() else Path('/home/flask/blog/ticker_motif_custom.json')


def load_ticker_motifs():
    data=json.loads(ticker_motifs_path().read_text(encoding='utf-8'))
    if not isinstance(data,dict) or not data:
        raise ValueError('Ticker motif asset must be a nonempty object')
    return data


def preflight():
    load_ticker_motifs()
    path=ticker_motifs_path()
    return {'ticker_motifs_path':str(path),'ticker_motifs_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


if __name__=='__main__':
    print(json.dumps(preflight()))
