// Field Guide's Mac launcher. make-app.sh compiles it (the default build) into Contents/MacOS, so that macOS shows
// the app's own name and icon in the Dock and the app switcher instead of "Python".
//
// It runs the checkout's app/server.py inside this process with the venv's Python. The code stays outside the
// bundle. make-app.sh passes the three absolute paths in as -DPYTHON_EXE, -DSERVER_SCRIPT and -DLOG_DIR.
#include <Python.h>
#include <fcntl.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef PYTHON_EXE
#error "compile with -DPYTHON_EXE=\"<venv>/bin/python\""
#endif
#ifndef LOG_DIR
#error "compile with -DLOG_DIR=\"<checkout>/logs\""
#endif
#ifndef SERVER_SCRIPT
#error "compile with -DSERVER_SCRIPT=\"<checkout>/app/server.py\""
#endif

int main(void) {
    // There is no terminal to print to, so keep a private log in the checkout's logs/ folder.
    mkdir(LOG_DIR, 0700);  // fails harmlessly when it already exists
    int fd = open(LOG_DIR "/app.log", O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd >= 0) {
        dup2(fd, STDOUT_FILENO);
        dup2(fd, STDERR_FILENO);
        if (fd > STDERR_FILENO) close(fd);
    }
    unsetenv("PYTHONPATH");  // nothing from the environment may add to the import path
    unsetenv("PYTHONHOME");
    setenv("PYTHONNOUSERSITE", "1", 1);
    // argv[0] is the venv's Python, so Python finds the venv's pyvenv.cfg and site-packages; -s skips the user site.
    char *args[] = {PYTHON_EXE, "-s", SERVER_SCRIPT, NULL};
    return Py_BytesMain(3, args);
}
