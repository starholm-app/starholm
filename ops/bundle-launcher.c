// Starholm's launcher inside the downloadable Mac app. ops/build-mac-app.sh compiles it into Contents/MacOS/Starholm,
// so macOS shows the app's own name and icon in the Dock and the app switcher instead of "Python".
//
// Unlike ops/launcher.c (the developer's own copy, which runs a checkout), nothing here is a path on the build Mac.
// Every path is found from this executable's own place in the bundle:
//   Contents/Resources/python/   the bundled Python (python-build-standalone) with the app's packages
//   Contents/Resources/starholm/ the app's code
//   Contents/Resources/poppler/  pdftotext and pdfinfo, for the free library's PDF manuals
// The bundle is never written to. Everything the app writes goes to one data folder:
//   ~/Library/Application Support/Starholm/, or the absolute folder in $STARHOLM_DATA_DIR (for tests)
// holding app-data/ (settings, chats, notes, indexes), data.noindex/ (the library, maps, packs), logs/ and your-files/.
#include <Python.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define PYTHON_VERSION_DIR "python3.13"
#define MAX_ARGS 16

static void die(const char *what) {
    fprintf(stderr, "Starholm can't start: %s\n", what);
    exit(1);
}

// "a" + "b" into out, or stop: every path here fits in PATH_MAX or the app doesn't start.
static void join(char *out, const char *a, const char *b) {
    if (snprintf(out, PATH_MAX, "%s%s", a, b) >= PATH_MAX) die("a path is too long");
}

// mkdir -p with the given mode for the folders it creates.
static void make_dirs(const char *path, mode_t mode) {
    char buf[PATH_MAX];
    join(buf, path, "");
    for (char *p = buf + 1; *p; p++) {
        if (*p != '/') continue;
        *p = '\0';
        if (mkdir(buf, mode) != 0 && errno != EEXIST) die("can't create the data folder");
        *p = '/';
    }
    if (mkdir(buf, mode) != 0 && errno != EEXIST) die("can't create the data folder");
}

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX], contents[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, real)) die("can't find its own executable");
    // real is <bundle>/Contents/MacOS/Starholm: cut the last two parts to get <bundle>/Contents.
    join(contents, real, "");
    for (int i = 0; i < 2; i++) {
        char *slash = strrchr(contents, '/');
        if (!slash || slash == contents) die("isn't inside an app bundle");
        *slash = '\0';
    }

    char python[PATH_MAX], server[PATH_MAX], poppler[PATH_MAX], path_var[PATH_MAX];
    join(python, contents, "/Resources/python/bin/" PYTHON_VERSION_DIR);
    join(server, contents, "/Resources/starholm/app/server.py");
    join(poppler, contents, "/Resources/poppler/bin");
    join(path_var, poppler, ":/usr/bin:/bin:/usr/sbin:/sbin");

    // The data folder: $STARHOLM_DATA_DIR when it is an absolute path, else Application Support.
    char root[PATH_MAX];
    const char *override = getenv("STARHOLM_DATA_DIR");
    if (override && override[0] == '/') {
        join(root, override, "");
    } else {
        const char *home = getenv("HOME");
        if (!home || home[0] != '/') {
            struct passwd *pw = getpwuid(getuid());
            home = pw ? pw->pw_dir : NULL;
        }
        if (!home || home[0] != '/') die("no home folder");
        join(root, home, "/Library/Application Support/Starholm");
    }
    umask(077);
    make_dirs(root, 0700);

    char app_dir[PATH_MAX], data_dir[PATH_MAX], log_dir[PATH_MAX], files_dir[PATH_MAX], log[PATH_MAX];
    join(app_dir, root, "/app-data");
    join(data_dir, root, "/data.noindex");
    join(log_dir, root, "/logs");
    join(files_dir, root, "/your-files");
    join(log, log_dir, "/app.log");

    // There is no terminal to print to, so keep a private log in the data folder.
    make_dirs(log_dir, 0700);
    int fd = open(log, O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd >= 0) {
        dup2(fd, STDOUT_FILENO);
        dup2(fd, STDERR_FILENO);
        if (fd > STDERR_FILENO) close(fd);
    }

    setenv("SURVIVAL_LLM_APP_DIR", app_dir, 1);
    setenv("SURVIVAL_LLM_DATA", data_dir, 1);
    setenv("SURVIVAL_LLM_LOGS", log_dir, 1);
    setenv("SURVIVAL_LLM_YOUR_FILES", files_dir, 1);
    // Folders a developer's shell might name: the app uses only its own data folder.
    unsetenv("SURVIVAL_LLM_LIBRARY");
    unsetenv("SURVIVAL_LLM_FILES");
    unsetenv("SURVIVAL_LLM_REFRESH_LOG");
    // Nothing from the environment may change which Python code runs.
    unsetenv("PYTHONPATH");
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONSTARTUP");
    setenv("PYTHONNOUSERSITE", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    setenv("PATH", path_var, 1);

    // argv[0] is the bundled Python, so Python finds its own standard library next to it. -E ignores PYTHON*
    // variables, -s skips the user site folder, -B never writes .pyc files (the build ships them). Arguments given to
    // the app are passed on (for example --no-browser), except the -psn_ one old macOS versions add.
    char *args[MAX_ARGS + 6] = {python, "-E", "-s", "-B", server};
    int n = 5;
    for (int i = 1; i < argc && n < MAX_ARGS + 5; i++) {
        if (strncmp(argv[i], "-psn_", 5) == 0) continue;
        args[n++] = argv[i];
    }
    args[n] = NULL;
    return Py_BytesMain(n, args);
}
