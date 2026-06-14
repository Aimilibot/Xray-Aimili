#!/usr/bin/env python3
import sys
from pathlib import Path

# Add the workspace root to sys.path to allow finding backend packages
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

# Import main entry point from refactored app and run it
from backend.app.main import main

if __name__ == "__main__":
    main()
