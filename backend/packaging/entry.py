"""PyInstaller 入口。"""

import multiprocessing
import sys

from tk_workspace.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
