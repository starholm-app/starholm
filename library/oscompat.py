"""What differs between macOS and Windows for the app's files and processes, kept in one place.

On macOS every helper does exactly what the code did before it moved here. The Windows branches serve the Windows app
(ops/build-windows-app.ps1): Windows 10 22H2 or newer, x64.

    NOFOLLOW, BINARY   flags for os.open. Windows has no O_NOFOLLOW (making a symlink there needs admin rights or
                       developer mode), and without O_BINARY it opens a file descriptor in text mode, which turns
                       "\\n" into "\\r\\n" on write
    is_link            a symlink, or on Windows a directory junction
    swap_link          points a link at a folder: a relative symlink on macOS, a junction on Windows (no admin rights)
    remove_link        removes a link, never the folder it points at
    try_lock, unlock   a non-blocking exclusive file lock: flock on macOS, msvcrt.locking on Windows
    lower_priority     runs this process below normal priority (os.nice on macOS)
    pid_gone           whether a process is certainly not running. On Windows os.kill(pid, 0) would END the process,
                       so it asks OpenProcess instead
    process_start      when a process started (Windows: GetProcessTimes; macOS keeps using ps in library/search.py)
    rss_bytes, peak_rss_bytes   memory use (Windows: GetProcessMemoryInfo; macOS keeps using ps and resource)

Importing this module on Windows also adjusts subprocess.Popen once, for every child process the app starts (see
_windows_children): no console window flashes up for a console program started from the windowed app, a child Python
reads and writes UTF-8 as the app does, and an environment the code gives a child keeps SYSTEMROOT, without which
Python and most Windows programs can't start.
It also makes os.replace, os.remove and os.unlink retry for a moment while another reader has the file open (see
_windows_replace and _windows_remove).
"""
import os
import subprocess
import sys

WINDOWS = sys.platform == "win32"
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
BINARY = getattr(os, "O_BINARY", 0)


# ---------- links ----------

def is_link(path):
    """Whether path is a symlink, or on Windows a directory junction (what swap_link makes there)."""
    if os.path.islink(path):
        return True
    return WINDOWS and os.path.isjunction(path)


def remove_link(path):
    """Remove the link at path itself, never what it points at."""
    if WINDOWS and os.path.isjunction(path):
        os.rmdir(path)  # removes the junction; the folder it points at is untouched
    else:
        os.remove(path)


def swap_link(target, link, tmp=None):
    """Point link at the folder target. macOS: a relative symlink made beside it (tmp) and renamed over it, so readers
    always see a whole link. Windows: a junction to the absolute path (a symlink there needs admin rights or developer
    mode); a junction can't be renamed over another, so the old one is removed first and the new one renamed into its
    place. Readers resolve the link once when they start, so the moment without one only matters to a reader starting
    in it, which then finds no library until the next start."""
    parent = os.path.dirname(link)
    tmp = tmp or os.path.join(parent, f".{os.path.basename(link)}-{os.getpid()}.tmp")
    if os.path.lexists(tmp):
        remove_link(tmp)
    if not WINDOWS:
        os.symlink(os.path.relpath(target, parent), tmp)
        os.replace(tmp, link)
        return
    import _winapi
    _winapi.CreateJunction(os.path.abspath(target), tmp)
    if os.path.lexists(link):
        remove_link(link)
    os.rename(tmp, link)


# ---------- file locks ----------

def try_lock(handle):
    """Take an exclusive lock on an open file without waiting: True when taken, False when another process holds it.
    Closing the file gives it back (on Windows, call unlock first: Windows may free it only a moment after the close)."""
    if WINDOWS:
        import msvcrt
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def unlock(handle):
    """Give back a lock try_lock took, before the file is closed (closing alone is enough on macOS)."""
    if WINDOWS:
        import msvcrt
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


# ---------- processes ----------

_STILL_ACTIVE = 259
_QUERY_LIMITED = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
_VM_READ = 0x0010


def _kernel32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    return k


def pid_gone(pid):
    """Whether the process pid is certainly not running. When it can't be told (no permission to ask), False: the
    callers then keep what belongs to it, which only costs disk."""
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        try:
            k = _kernel32()
            h = k.OpenProcess(_QUERY_LIMITED, False, int(pid))
        except (OSError, OverflowError, ValueError):
            return False
        if not h:
            return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no process has this id
        try:
            code = wintypes.DWORD()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value != _STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OverflowError, OSError):
        return False
    return False


def lower_priority():
    """Run this process below normal priority, so a long build leaves the computer usable: os.nice(10) on macOS,
    BELOW_NORMAL_PRIORITY_CLASS on Windows (which has no os.nice). On Windows it never raises."""
    if not WINDOWS:
        os.nice(10)
        return
    from ctypes import wintypes
    try:
        k = _kernel32()
        k.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
    except (OSError, AttributeError):
        pass


def process_start(pid):
    """Windows only: when process pid started, as a string that differs between two processes with the same id, or
    None when it has ended or can't be asked."""
    import ctypes
    from ctypes import wintypes
    try:
        k = _kernel32()
        h = k.OpenProcess(_QUERY_LIMITED, False, int(pid))
    except (OSError, OverflowError, ValueError):
        return None
    if not h:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(h, *(ctypes.byref(t) for t in times)):
            return None
        code = wintypes.DWORD()
        if k.GetExitCodeProcess(h, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            return None
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)  # creation time, 100 ns since 1601
    finally:
        k.CloseHandle(h)


def _memory_counters(pid=None):
    """Windows only: (working set, peak working set) in bytes of pid (default: this process), or (0, 0)."""
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    try:
        k = _kernel32()
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        own = pid is None
        h = k.GetCurrentProcess() if own else k.OpenProcess(_QUERY_LIMITED | _VM_READ, False, int(pid))
        if not h:
            return 0, 0
        try:
            c = Counters()
            c.cb = ctypes.sizeof(c)
            psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
            if not psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                return 0, 0
            return c.WorkingSetSize, c.PeakWorkingSetSize
        finally:
            if not own:
                k.CloseHandle(h)
    except (OSError, OverflowError, ValueError, AttributeError):
        return 0, 0


def rss_bytes(pid):
    """Windows only: a process's resident memory in bytes (0 if it can't tell)."""
    return _memory_counters(pid)[0]


def peak_rss_bytes():
    """Windows only: this process's peak resident memory in bytes."""
    return _memory_counters()[1]


# ---------- Windows only: the app's own program, the Recycle Bin ----------

def own_executable():
    """Windows only: the path of this process's program (Starholm.exe in the Windows app), or None."""
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    try:
        n = ctypes.WinDLL("kernel32", use_last_error=True).GetModuleFileNameW(None, buf, len(buf))
    except (OSError, AttributeError):
        return None
    return os.path.realpath(buf.value) if 0 < n < len(buf) else None


def recycle(path):
    """Windows only: move a file to the Recycle Bin (SHFileOperationW with FOF_ALLOWUNDO), so it can be put back."""
    import ctypes
    from ctypes import wintypes

    class FileOp(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    fo_delete, flags = 3, 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO, NOCONFIRMATION, SILENT, NOERRORUI
    op = FileOp(None, fo_delete, os.path.abspath(path) + "\0", None, flags, False, None, None)  # list ends in \0\0
    code = ctypes.WinDLL("shell32").SHFileOperationW(ctypes.byref(op))
    if code or op.fAnyOperationsAborted:
        raise OSError(f"couldn't move the file to the Recycle Bin (code {code})")


# ---------- child processes on Windows ----------

def _windows_children():
    """Wrap subprocess.Popen so that, on Windows, every child process:
    - gets CREATE_NO_WINDOW unless the caller chose its own creation flags: the app runs without a console, and a
      console program it starts (pdftotext, ollama, a child Python) would otherwise open a console window;
    - when it is this Python (sys.executable), runs with -X utf8, as the app itself does (its launcher turns UTF-8 mode
      on), since the -I the app passes makes a child ignore PYTHONUTF8;
    - keeps SYSTEMROOT when the caller passes an environment without it."""
    original = subprocess.Popen.__init__

    def init(self, args, *rest, **kwargs):
        if not kwargs.get("creationflags") and not (len(rest) > 12 and rest[12]):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        if isinstance(args, (list, tuple)) and args and str(args[0]) == sys.executable and "utf8" not in args[1:3]:
            args = [args[0], "-X", "utf8", *args[1:]]
        env = kwargs.get("env")
        if env is not None and not any(k.upper() == "SYSTEMROOT" for k in env) and os.environ.get("SYSTEMROOT"):
            kwargs["env"] = {**env, "SYSTEMROOT": os.environ["SYSTEMROOT"]}
        original(self, args, *rest, **kwargs)

    if not getattr(subprocess.Popen, "_starholm_windows", False):
        subprocess.Popen.__init__ = init
        subprocess.Popen._starholm_windows = True


def _windows_replace():
    """Make os.replace wait for readers on Windows. There, replacing a file fails (access denied, or the file is in use)
    while another thread or process has it open, as when the interface reads a status file the moment it is rewritten.
    macOS replaces it under the reader. Each such failure is retried for up to about two seconds before it is raised."""
    import time
    original = os.replace

    def replace(src, dst, *args, **kwargs):
        for _ in range(40):
            try:
                return original(src, dst, *args, **kwargs)
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32):  # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION
                    raise
                time.sleep(0.05)
        return original(src, dst, *args, **kwargs)

    if not getattr(os.replace, "_starholm_windows", False):
        replace._starholm_windows = True
        os.replace = replace


def _windows_remove():
    """The same for os.remove and os.unlink: deleting a file fails on Windows while another thread has it open (the
    download page reading a split file's segment record the moment the finished file's record is deleted)."""
    import time

    def patient(original):
        def remove(path, *args, **kwargs):
            for _ in range(40):
                try:
                    return original(path, *args, **kwargs)
                except PermissionError as exc:
                    if getattr(exc, "winerror", None) != 32:  # ERROR_SHARING_VIOLATION only: access denied stays
                        raise
                    time.sleep(0.05)
            return original(path, *args, **kwargs)
        remove._starholm_windows = True
        return remove

    for name in ("remove", "unlink"):
        if not getattr(getattr(os, name), "_starholm_windows", False):
            setattr(os, name, patient(getattr(os, name)))


if WINDOWS:
    _windows_children()
    _windows_replace()
    _windows_remove()


def windows_apps_dark():
    """Windows: True when the person has chosen dark mode for apps (Settings > Personalisation > Colours). Raises
    OSError when the setting isn't there (an older Windows); the caller then uses its own default."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
        return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
