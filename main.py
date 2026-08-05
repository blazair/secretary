"""
main.py
=======
Entry point:  python main.py

Reading order for the code, simplest first:
    models.py       the data shapes
    quick_add.py    text into a task
    storage.py      the files on disk
    calibration.py  the measured numbers
    scheduler.py    tasks into a day
    capacity.py     numbers into a message
    ui.py           the window
"""

import storage
from ui import run_application


def main() -> None:
    storage.ensure_data_directory()
    # Written out on first run so that settings.json exists on disk and can
    # be edited by hand.
    storage.save_settings(storage.load_settings())
    run_application()


if __name__ == "__main__":
    main()
