"""Normal-owned shared budget lease; no generic legacy/foreign retirement."""
from contextlib import contextmanager
from pathlib import Path
import ctypes,json,os,re,sys,uuid
from daily_budget import BudgetEvidenceError,_unlinked,read

def identity(path):
    s=Path(path).stat(follow_symlinks=False)
    return {k:getattr(s,k) for k in ('st_dev','st_ino','st_uid','st_gid')}
def sync_dir(path):
    if os.name=='posix':
        fd=os.open(path,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
def rename_noreplace(source,target):
    if sys.platform=='linux':
        libc=ctypes.CDLL(None,use_errno=True);fn=getattr(libc,'renameat2',None)
        if fn is None:raise BudgetEvidenceError('Atomic normal lease handoff unavailable')
        fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
        if fn(-100,os.fsencode(source),-100,os.fsencode(target),1):
            error=ctypes.get_errno();raise OSError(error,os.strerror(error),str(target))
    elif os.name=='nt':os.rename(source,target)
    else:raise BudgetEvidenceError('Native atomic normal lease handoff unsupported')
def process_identity(pid):
    if type(pid) is not int or pid<1:raise BudgetEvidenceError('Invalid normal lease process')
    if sys.platform=='linux':
        boot=Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
        try:fields=Path('/proc/%d/stat'%pid).read_text(encoding='ascii').rsplit(')',1)[1].split()
        except FileNotFoundError:return None
        return {'platform':'linux','boot_id':boot,'start_ticks':fields[19]}
    if os.name=='nt':
        from ctypes import wintypes
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];k.OpenProcess.restype=wintypes.HANDLE
        handle=k.OpenProcess(0x1000,False,pid)
        if not handle:
            if ctypes.get_last_error()==87:return None
            raise BudgetEvidenceError('Normal lease process identity unavailable')
        try:
            created=wintypes.FILETIME();exited=wintypes.FILETIME();kernel=wintypes.FILETIME();user=wintypes.FILETIME()
            k.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)];k.GetProcessTimes.restype=wintypes.BOOL
            if not k.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel),ctypes.byref(user)):
                raise BudgetEvidenceError('Normal lease process timing unavailable')
            return {'platform':'windows','creation_filetime':(created.dwHighDateTime<<32)|created.dwLowDateTime}
        finally:
            k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle(handle)
    raise BudgetEvidenceError('Native normal lease process identity unsupported')
@contextmanager
def kernel_lock(registry):
    path=registry/'.normal-reservation.lock';header=b'SMN normal shared budget kernel lock v1\n';_unlinked(path)
    if not path.exists():
        temporary=registry/('.normal-lock-prepared-'+uuid.uuid4().hex)
        with temporary.open('xb') as f:f.write(header);f.flush();os.fsync(f.fileno())
        try:os.link(temporary,path)
        except FileExistsError:pass
        finally:temporary.unlink()
        sync_dir(registry)
    _unlinked(path);fd=os.open(path,os.O_RDWR|getattr(os,'O_NOFOLLOW',0));locked=False
    try:
        opened=os.fstat(fd);current=identity(path)
        if (opened.st_dev,opened.st_ino)!=(current['st_dev'],current['st_ino']):
            raise BudgetEvidenceError('Unknown normal kernel lock custody retained')
        try:
            if os.name=='posix':
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            elif os.name=='nt':
                import msvcrt
                os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:raise BudgetEvidenceError('Native budget kernel lock unsupported')
            locked=True
        except (BlockingIOError,OSError) as exc:raise BudgetEvidenceError('Active normal budget kernel lock; retain it') from exc
        if identity(path)!=current or os.read(fd,4096)!=header:
            raise BudgetEvidenceError('Unknown or changed normal kernel lock custody retained')
        yield current
    finally:
        if locked:
            if os.name=='posix':fcntl.flock(fd,fcntl.LOCK_UN)
            else:os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
        os.close(fd)
def recognized(lock,registry,kernel):
    _unlinked(lock)
    if not lock.is_dir() or {p.name for p in lock.iterdir()}!={'normal-owner.json'}:
        raise BudgetEvidenceError('Legacy or malformed shared budget lease retained')
    _unlinked(lock/'normal-owner.json')
    try:owner=read(lock/'normal-owner.json')
    except (OSError,ValueError) as exc:raise BudgetEvidenceError('Unreadable normal lease owner retained') from exc
    if not isinstance(owner,dict):raise BudgetEvidenceError('Malformed normal lease owner retained')
    if (owner.get('kind')!='smn-normal-budget-lease-v1' or not re.fullmatch('[0-9a-f]{32}',str(owner.get('token',''))) or
        owner.get('registry')!=str(registry) or owner.get('kernel_identity')!=kernel or owner.get('directory_identity')!=identity(lock) or
        any(identity(lock/'normal-owner.json')[key]!=owner['directory_identity'][key] for key in ('st_uid','st_gid')) or
        type(owner.get('pid')) is not int or owner.get('pid',0)<1 or not isinstance(owner.get('process_identity'),dict)):
        raise BudgetEvidenceError('Foreign or changed normal lease identity retained')
    process=owner['process_identity']
    linux=(sys.platform=='linux' and set(process)=={'platform','boot_id','start_ticks'} and process['platform']=='linux' and
        isinstance(process['boot_id'],str) and re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',process['boot_id']) and
        isinstance(process['start_ticks'],str) and re.fullmatch('[0-9]+',process['start_ticks']))
    windows=(os.name=='nt' and set(process)=={'platform','creation_filetime'} and process['platform']=='windows' and
        type(process['creation_filetime']) is int and process['creation_filetime']>0)
    if not (linux or windows):raise BudgetEvidenceError('Unknown or malformed normal lease process proof retained')
    return owner
def retain(lock,registry,owner,outcome):
    history=registry/'normal-lease-history';_unlinked(history);history.mkdir(exist_ok=True)
    rename_noreplace(lock,history/(owner['token']+'-'+outcome));sync_dir(registry);sync_dir(history)
@contextmanager
def lease(registry):
    # This stable native lock releases on process death; it alone never grants
    # permission to remove a bare recovery directory or an unknown normal owner.
    with kernel_lock(registry) as kernel:
        lock=registry/'.reservation-lease'
        if lock.exists():
            prior=recognized(lock,registry,kernel)
            if process_identity(prior['pid'])==prior['process_identity']:
                raise BudgetEvidenceError('Active normal budget owner; retain its lease')
            retain(lock,registry,prior,'interrupted')
        token=uuid.uuid4().hex;prepared=registry/('.normal-lease-prepared-'+token);prepared.mkdir()
        owner={'kind':'smn-normal-budget-lease-v1','token':token,'pid':os.getpid(),'process_identity':process_identity(os.getpid()),
            'registry':str(registry),'directory_identity':identity(prepared),'kernel_identity':kernel}
        with (prepared/'normal-owner.json').open('xb') as f:
            f.write((json.dumps(owner,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
        sync_dir(prepared)
        try:rename_noreplace(prepared,lock)
        except OSError as exc:raise BudgetEvidenceError('Existing shared budget lease retained; never steal it') from exc
        sync_dir(registry)
        try:yield
        finally:
            current=recognized(lock,registry,kernel)
            if current!=owner:raise BudgetEvidenceError('Normal lease peer drift; retain custody')
            retain(lock,registry,owner,'completed')
