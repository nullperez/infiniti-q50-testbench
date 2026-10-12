"""Stream every frame (RX and TX, all buses) to a pcapng file, plus a JSONL
sidecar for markers/annotations and bench events on the same timebase."""

from __future__ import annotations

import json
import queue
import threading
from typing import Optional

from q50tb.capture.formats import PcapngWriter
from q50tb.can.frame import CanFrame
from q50tb.core import timebase


class Recorder:
    def __init__(self):
        self._writer: Optional[PcapngWriter] = None
        self._q: "queue.Queue[Optional[CanFrame]]" = queue.Queue(maxsize=200_000)
        self._thread: Optional[threading.Thread] = None
        self.path = ""
        self.dropped = 0
        self._markers = None

    @property
    def active(self) -> bool:
        return self._writer is not None

    @property
    def count(self) -> int:
        return self._writer.count if self._writer else 0

    def start(self, path: str) -> None:
        self.stop()
        self.path = path
        self._writer = PcapngWriter(path)
        self._markers = open(path + ".markers.jsonl", "a", encoding="utf-8")
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._thread.start()
        self.marker("recording started")

    def stop(self) -> None:
        if self._writer is None:
            return
        self.marker("recording stopped")
        self._q.put(None)
        if self._thread:
            self._thread.join(timeout=3)
        self._writer.close()
        self._writer = None
        if self._markers:
            self._markers.close()
            self._markers = None

    def on_frame(self, frame: CanFrame) -> None:
        if self._writer is None:
            return
        try:
            self._q.put_nowait(frame)
        except queue.Full:
            self.dropped += 1

    def marker(self, text: str, **extra) -> None:
        if self._markers:
            t = timebase.now()
            self._markers.write(json.dumps({"t": round(t, 6), "unix": timebase.wall(t), "text": text, **extra}) + "\n")
            self._markers.flush()

    def _run(self) -> None:
        w = self._writer
        n = 0
        while True:
            f = self._q.get()
            if f is None:
                break
            w.write(f, unix_time=timebase.wall(f.timestamp))
            n += 1
            if n % 2000 == 0:
                w.flush()
        w.flush()
