"""Streamlit-Server für die Desktop-App mit Wächter: endet automatisch, wenn der Launcher endet.

Hintergrund: Stürzt das Fenster ab (z. B. SIGABRT der Web-Engine), laufen ``atexit``-Handler
nicht mehr. Ohne Wächter bliebe der Server als verwaister Prozess im Hintergrund.
Aufruf (durch den Launcher): ``python -m desktop.server --eltern-pid <PID> -- <streamlit-Argumente>``.
"""

from __future__ import annotations

import os
import sys
import threading
import time


def _waechter(eltern_pid: int, intervall: float = 1.0) -> None:
    while True:
        try:
            os.kill(eltern_pid, 0)                    # existiert der Launcher noch?
        except ProcessLookupError:
            os._exit(0)
        except PermissionError:                       # Prozess existiert (anderer Besitzer) – weiter
            pass
        if os.getppid() != eltern_pid:                # neu zugeordnet (launchd/init) → Launcher ist weg
            os._exit(0)
        time.sleep(intervall)


def main(argv: list[str] | None = None) -> int:
    argumente = list(sys.argv[1:] if argv is None else argv)
    if len(argumente) < 3 or argumente[0] != "--eltern-pid" or "--" not in argumente:
        print("Aufruf: python -m desktop.server --eltern-pid <PID> -- <streamlit-Argumente>", file=sys.stderr)
        return 2
    eltern_pid = int(argumente[1])
    streamlit_argumente = argumente[argumente.index("--") + 1:]
    threading.Thread(target=_waechter, args=(eltern_pid,), daemon=True, name="eltern-waechter").start()

    from streamlit.web import cli  # noqa: PLC0415

    sys.argv = ["streamlit", *streamlit_argumente]
    return cli.main()


if __name__ == "__main__":
    sys.exit(main())
