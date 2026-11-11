"""Background drafting of cross-ex questions.

Streamlit reruns the script on every interaction, so the Claude call runs on its own thread
and leaves its result here. The app polls this state every few seconds. The app is a local,
single-user program, so one process-wide job slot is enough.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

_lock = threading.Lock()
STATE: dict = {
    "running": False,
    "round_id": None,     # which round the current or last job belongs to
    "sig": "",            # flow signature the current or last job was started from
    "target": "",         # opponent speech the questions are for, e.g. "1AC"
    "questions": None,    # list of {"question", "purpose"} once finished
    "error": None,
    "finished_at": None,
    "collected": True,    # True once the app has copied the result into the round
}


def snapshot() -> dict:
    with _lock:
        return dict(STATE)


def start(round_id: str, sig: str, target: str, fn: Callable[[], list[dict]]) -> bool:
    """Run fn on a background thread. Returns False if a job is already running."""
    with _lock:
        if STATE["running"]:
            return False
        STATE.update(running=True, round_id=round_id, sig=sig, target=target,
                     questions=None, error=None, finished_at=None, collected=False)

    def work() -> None:
        try:
            qs = fn()
            err = None
        except Exception as e:  # noqa: BLE001
            qs, err = None, str(e)
        with _lock:
            STATE.update(running=False, questions=qs, error=err, finished_at=time.time())

    threading.Thread(target=work, daemon=True).start()
    return True


def mark_collected() -> None:
    with _lock:
        STATE["collected"] = True
