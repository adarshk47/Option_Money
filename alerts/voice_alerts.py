"""Voice alerts via pyttsx3 (offline TTS). One worker thread, queued."""
from __future__ import annotations

import queue
import threading

from backend.logger import get_logger

log = get_logger(__name__)


class VoiceAlert:
    def __init__(self) -> None:
        import pyttsx3  # raises ImportError if unavailable -> channel skipped
        self._pyttsx3 = pyttsx3
        self._q: queue.Queue[str] = queue.Queue(maxsize=10)
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        while True:
            text = self._q.get()
            try:
                engine = self._pyttsx3.init()
                engine.setProperty("rate", 165)
                engine.say(text)
                engine.runAndWait()
                engine.stop()
            except Exception:
                log.exception("TTS playback failed")

    def send(self, title: str, message: str, speak: str | None = None) -> None:
        text = speak or title
        try:
            self._q.put_nowait(text)
        except queue.Full:
            pass  # never block trading on a chatty queue
