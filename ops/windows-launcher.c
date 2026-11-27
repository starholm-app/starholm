// Starholm's launcher in the Windows app. ops/build-windows-app.ps1 compiles it into Starholm.exe with the app's icon,
// as a windowed program (no console), so Windows shows Starholm's own name and icon in the taskbar and Start menu.
//
// It is the Windows counterpart of ops/bundle-launcher.c. Every path is found from this program's own folder:
//   python\           the bundled Python (python-build-standalone) with the app's packages
//   starholm\         the app's code
//   poppler\bin\      pdftotext.exe and pdfinfo.exe, for the free library's PDF manuals
// The install folder is never written to. Everything the app writes goes to one data folder:
//   %LOCALAPPDATA%\Starholm\, or the absolute folder in %STARHOLM_DATA_DIR% (for tests)
// holding app-data\ (settings, chats, notes, indexes), data.noindex\ (the library, maps, packs), logs\ and your-files\.
//
// Python runs inside this process (python313.dll, loaded from python\ when first called: it is delay-loaded), with
// UTF-8 mode on and sys.executable set to python\pythonw.exe, so a child Python the app starts opens no console.
#define WIN32_LEAN_AND_MEAN
#include <Python.h>  // first, as Python's documentation asks
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <fcntl.h>
#include <io.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <wchar.h>

#define LONG_PATH 32768
#define MAX_ARGS 16

static wchar_t log_path[LONG_PATH];  // logs\app.log, once the data folder is known

// Append a line to a file (UTF-8), quietly.
static void note(const wchar_t *path, const wchar_t *text) {
    FILE *f = NULL;
    if (path && path[0] && _wfopen_s(&f, path, L"a, ccs=UTF-8") == 0 && f) {
        fwprintf(f, L"%ls\n", text);
        fclose(f);
    }
}

// Say why the app can't start: in the log (or, before there is one, %TEMP%\Starholm-start-error.log), then in a
// message box.
static void die(const wchar_t *what) {
    wchar_t text[512], temp[MAX_PATH + 40];
    _snwprintf_s(text, 512, _TRUNCATE, L"Starholm can't start: %ls", what);
    DWORD t = GetTempPathW(MAX_PATH, temp);
    if (t > 0 && t < MAX_PATH) {
        wcscat_s(temp, MAX_PATH + 40, L"Starholm-start-error.log");
        note(temp, text);
    }
    note(log_path, text);
    MessageBoxW(NULL, text, L"Starholm", MB_OK | MB_ICONERROR);
    ExitProcess(1);
}

// a + b into out (LONG_PATH wide characters), or stop.
static void join(wchar_t *out, const wchar_t *a, const wchar_t *b) {
    if (_snwprintf_s(out, LONG_PATH, _TRUNCATE, L"%ls%ls", a, b) < 0) die(L"a path is too long");
}

static void set_env(const wchar_t *name, const wchar_t *value) {
    if (_wputenv_s(name, value ? value : L"") != 0) die(L"can't set up its environment");
}

static void make_dirs(const wchar_t *path) {
    int r = SHCreateDirectoryExW(NULL, path, NULL);
    if (r != ERROR_SUCCESS && r != ERROR_ALREADY_EXISTS && r != ERROR_FILE_EXISTS) die(L"can't create the data folder");
}

static int absolute(const wchar_t *p) {  // C:\... or \\server\share\...
    return p && ((iswalpha(p[0]) && p[1] == L':' && (p[2] == L'\\' || p[2] == L'/')) || (p[0] == L'\\' && p[1] == L'\\'));
}

static void check_status(PyStatus status) {
    if (PyStatus_Exception(status)) {
        if (PyStatus_IsExit(status)) ExitProcess((UINT)status.exitcode);
        Py_ExitStatusException(status);
    }
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show) {
    (void)instance; (void)previous; (void)command_line; (void)show;
    static wchar_t exe[LONG_PATH], home[LONG_PATH], python_dir[LONG_PATH], pythonw[LONG_PATH], server[LONG_PATH];
    static wchar_t poppler[LONG_PATH], path_var[LONG_PATH], root[LONG_PATH], buf[LONG_PATH];
    DWORD n = GetModuleFileNameW(NULL, exe, LONG_PATH);
    if (n == 0 || n >= LONG_PATH) die(L"can't find its own program");
    join(home, exe, L"");
    wchar_t *slash = wcsrchr(home, L'\\');
    if (!slash) die(L"can't find its own folder");
    *slash = L'\0';

    join(python_dir, home, L"\\python");
    join(pythonw, python_dir, L"\\pythonw.exe");
    join(server, home, L"\\starholm\\app\\server.py");
    join(poppler, home, L"\\poppler\\bin");
    if (GetFileAttributesW(server) == INVALID_FILE_ATTRIBUTES) die(L"its files are missing. Install it again.");
    // python313.dll and the runtime it needs are in python\: look there first, before anything is loaded from it.
    if (!SetDllDirectoryW(python_dir)) die(L"can't find its Python");

    // The data folder: %STARHOLM_DATA_DIR% when it is an absolute path, else %LOCALAPPDATA%\Starholm.
    DWORD got = GetEnvironmentVariableW(L"STARHOLM_DATA_DIR", buf, LONG_PATH);
    if (got > 0 && got < LONG_PATH && absolute(buf)) {
        join(root, buf, L"");
    } else {
        PWSTR local = NULL;
        if (SHGetKnownFolderPath(&FOLDERID_LocalAppData, KF_FLAG_CREATE, NULL, &local) != S_OK || !local)
            die(L"no local application data folder");
        join(root, local, L"\\Starholm");
        CoTaskMemFree(local);
    }
    make_dirs(root);

    join(buf, root, L"\\app-data");
    set_env(L"SURVIVAL_LLM_APP_DIR", buf);
    join(buf, root, L"\\data.noindex");
    set_env(L"SURVIVAL_LLM_DATA", buf);
    join(buf, root, L"\\your-files");
    set_env(L"SURVIVAL_LLM_YOUR_FILES", buf);
    join(buf, root, L"\\logs");
    set_env(L"SURVIVAL_LLM_LOGS", buf);
    make_dirs(buf);

    // There is no console to print to, so keep a log in the data folder (stdout and stderr both go there).
    join(buf, root, L"\\logs\\app.log");
    join(log_path, buf, L"");
    int fd = _wopen(buf, _O_WRONLY | _O_CREAT | _O_APPEND | _O_BINARY | _O_NOINHERIT, _S_IREAD | _S_IWRITE);
    if (fd >= 0) {
        _dup2(fd, 1);
        _dup2(fd, 2);
        if (fd > 2) _close(fd);
        SetStdHandle(STD_OUTPUT_HANDLE, (HANDLE)_get_osfhandle(1));
        SetStdHandle(STD_ERROR_HANDLE, (HANDLE)_get_osfhandle(2));
    }

    // Folders a developer's shell might name: the app uses only its own data folder.
    set_env(L"SURVIVAL_LLM_LIBRARY", NULL);
    set_env(L"SURVIVAL_LLM_FILES", NULL);
    set_env(L"SURVIVAL_LLM_REFRESH_LOG", NULL);
    // Nothing from the environment may change which Python code runs (Python ignores PYTHON* anyway: see below).
    set_env(L"PYTHONPATH", NULL);
    set_env(L"PYTHONHOME", NULL);
    set_env(L"PYTHONSTARTUP", NULL);
    set_env(L"PYTHONNOUSERSITE", L"1");
    set_env(L"PYTHONDONTWRITEBYTECODE", L"1");
    set_env(L"PYTHONUTF8", L"1");
    // PATH: Poppler first, then Windows' own folders only.
    wchar_t sysroot[MAX_PATH];
    UINT s = GetSystemWindowsDirectoryW(sysroot, MAX_PATH);
    if (s == 0 || s >= MAX_PATH) die(L"can't find the Windows folder");
    if (_snwprintf_s(path_var, LONG_PATH, _TRUNCATE, L"%ls;%ls\\System32;%ls;%ls\\System32\\Wbem", poppler, sysroot,
                     sysroot, sysroot) < 0) die(L"a path is too long");
    set_env(L"PATH", path_var);

    // UTF-8 mode, as PYTHONUTF8=1 would give: the app's files and pipes are UTF-8 whatever the Windows code page.
    PyPreConfig pre;
    PyPreConfig_InitPythonConfig(&pre);
    pre.utf8_mode = 1;
    pre.use_environment = 0;
    check_status(Py_PreInitialize(&pre));

    // Like the Mac launcher's -E -s -B: no PYTHON* variables, no user site folder, no .pyc written (the build ships
    // them). Arguments given to the app are passed on (for example --no-browser).
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.use_environment = 0;
    config.user_site_directory = 0;
    config.write_bytecode = 0;
    config.install_signal_handlers = 1;
    config.buffered_stdio = 0;  // the log gets each line as it is written
    check_status(PyConfig_SetString(&config, &config.home, python_dir));
    check_status(PyConfig_SetString(&config, &config.executable, pythonw));
    check_status(PyConfig_SetString(&config, &config.program_name, pythonw));
    int argc = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) die(L"can't read its arguments");
    wchar_t *args[MAX_ARGS + 2] = {pythonw, server};
    int count = 2;
    for (int i = 1; i < argc && count < MAX_ARGS + 2; i++) args[count++] = argv[i];
    check_status(PyConfig_SetArgv(&config, count, args));
    check_status(Py_InitializeFromConfig(&config));
    PyConfig_Clear(&config);
    return Py_RunMain();
}
