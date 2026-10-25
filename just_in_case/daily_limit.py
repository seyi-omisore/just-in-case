"""A simple daily limit on AI replies for a hosted site, shared by all visitors.

Counted in memory: it starts again each day (and when the server restarts), so it is a
safety net, not a bill. The OpenRouter credit limit on the key is the hard cap.
"""

import threading
from datetime import date


class DailyLimit:
    def __init__(self, limit: int, today=date.today):
        self.limit, self.today = max(0, int(limit)), today
        self.day, self.used = today(), 0
        self.lock = threading.Lock()

    def _roll(self) -> None:
        if self.today() != self.day:
            self.day, self.used = self.today(), 0

    def reached(self) -> bool:
        with self.lock:
            self._roll()
            return self.used >= self.limit

    def take(self) -> bool:
        """Use one AI reply. False (and nothing used) once today's limit is reached."""
        with self.lock:
            self._roll()
            if self.used >= self.limit:
                return False
            self.used += 1
            return True
