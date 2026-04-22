import sys
from pathlib import Path
import tkinter as tk

# Supports launching both:
# - python main_app/main.py
# - python -m main_app.main
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from main_app.paths import PROJECT_ROOT
assert PROJECT_ROOT == _repo_root  # bootstrap path must match paths.py

from main_app.ui.app_ui import DripCADUI


def main():
    root = tk.Tk()
    app = DripCADUI(root)
    root.mainloop()
    return app


if __name__ == "__main__":
    main()

