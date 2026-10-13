"""Optional internal dated-volume receiver, separate from article publication."""
from datetime import datetime,timezone
import csv
import socket
import stat
import struct
from pathlib import Path
from volume_feed_protocol import install_bundle,verified_peer,digest,COMMIT


def existing_nginx_peer(peer_socket,expected_uid,*,pid_file='/run/nginx.pid'):
    """Kernel-bound existing worker identity, not a caller-provided header."""
    try:
        if peer_socket is None or peer_socket.family!=socket.AF_UNIX or not hasattr(socket,'SO_PEERCRED'):
            return False
        uid=int(expected_uid)
        if uid<0 or str(uid)!=str(expected_uid):return False
        pid_path=Path(pid_file)
        info=pid_path.stat()
        if pid_path.is_symlink() or info.st_uid!=0 or stat.S_IMODE(info.st_mode)&0o022:
            return False
        master=int(pid_path.read_text());pid,peer_uid,gid=struct.unpack('3i',peer_socket.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if pid<=0 or peer_uid!=uid:return False
        process=Path('/proc')/str(pid)
        if (process/'exe').resolve()!=Path('/usr/sbin/nginx').resolve():return False
        fields=(process/'stat').read_text().rsplit(')',1)[1].split()
        return int(fields[1])==master
    except (OSError,ValueError,TypeError,AttributeError,IndexError,struct.error):
        return False


def receive_dated_volume(data, *, remote_addr, real_ip, producer_ip, directory,
                         expected_producer_commit,proxy_transport='direct',proxy_uid=None,peer_socket=None,now=None):
    # Production preflight must prove loopback comes only from the existing
    # private nginx listener, which overwrites X-Real-IP from the socket peer.
    # External socket peers cannot authorize themselves using that header.
    kernel_peer=existing_nginx_peer(peer_socket,proxy_uid) if proxy_transport=='unix' else False
    if not verified_peer(remote_addr, real_ip, producer_ip,proxy_transport=proxy_transport,verified_unix_peer=kernel_peer):
        return {'error':'untrusted_volume_producer'},403
    if not isinstance(expected_producer_commit,str) or not COMMIT.fullmatch(expected_producer_commit):
        return {'error':'volume_producer_revision_not_configured'},503
    try:
        if any(item['provenance']['source_commit']!=expected_producer_commit for item in data['files'].values()):
            raise ValueError('Producer differs from approved revision')
        result=install_bundle(directory,data,now=now or datetime.now(timezone.utc),
            attribution={'source_peer_ip':producer_ip,
                         'proxy_transport':proxy_transport,
                         'receiver_module_sha256':digest(Path(__file__).read_bytes())})
        return result,200
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,csv.Error) as error:
        return {'error':'invalid_volume_delivery','failure_type':type(error).__name__},400
    except OSError:
        return {'error':'volume_delivery_unavailable'},503
