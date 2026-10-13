"""Optional internal dated-volume receiver, separate from article publication."""
from datetime import datetime,timezone
import csv
from pathlib import Path
from volume_feed_protocol import install_bundle,verified_peer,digest,COMMIT


def receive_dated_volume(data, *, remote_addr, real_ip, producer_ip, directory,
                         expected_producer_commit,now=None):
    # Production preflight must prove loopback comes only from the existing
    # private nginx listener, which overwrites X-Real-IP from the socket peer.
    # External socket peers cannot authorize themselves using that header.
    if not verified_peer(remote_addr, real_ip, producer_ip):
        return {'error':'untrusted_volume_producer'},403
    if not COMMIT.fullmatch(expected_producer_commit):
        return {'error':'volume_producer_revision_not_configured'},503
    try:
        if any(item['provenance']['source_commit']!=expected_producer_commit for item in data['files'].values()):
            raise ValueError('Producer differs from approved revision')
        result=install_bundle(directory,data,now=now or datetime.now(timezone.utc),
            attribution={'source_peer_ip':producer_ip,
                         'receiver_module_sha256':digest(Path(__file__).read_bytes())})
        return result,200
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,csv.Error) as error:
        return {'error':'invalid_volume_delivery','failure_type':type(error).__name__},400
    except OSError:
        return {'error':'volume_delivery_unavailable'},503
