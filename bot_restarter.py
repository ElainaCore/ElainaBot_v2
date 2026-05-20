import os
import subprocess
import sys
import time
from contextlib import suppress


def main():
    time.sleep(3)
    main_path = r"main.py"
    os.chdir(os.path.dirname(main_path))
    subprocess.Popen([sys.executable, main_path], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                     cwd=os.path.dirname(main_path))
    time.sleep(1)
    with suppress(Exception):
        os.remove(__file__)
    sys.exit(0)


if __name__ == "__main__":
    main()
