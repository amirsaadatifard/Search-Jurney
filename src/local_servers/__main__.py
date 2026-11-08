"""Run only the bundled sample website: ``python -m src.local_servers``.

Handy for looking at the sample site in a normal browser. The same safety
rules apply as inside the application.
"""

from __future__ import annotations

import sys
import time

from src.app.bootstrap import bootstrap, shutdown


def main() -> int:
    container = bootstrap()
    try:
        errors = container.servers.start_all()
        for status in container.servers.statuses():
            print(
                f"{status.name:<20} {status.url}  {'running' if status.running else 'NOT RUNNING'}"
            )
        if errors:
            return 1
        print("Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        shutdown(container)


if __name__ == "__main__":
    sys.exit(main())
