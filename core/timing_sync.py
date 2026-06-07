import time


class TimingSync:
    def __init__(self, interval: int, start_monotonic: float | None = None):
        self.interval = interval
        # Ortak oturum epoch'u bootstrap sonunda yerel monotonic zamana çevrilir.
        self.start_time = start_monotonic if start_monotonic is not None else time.monotonic()

    def get_current_step(self) -> int:
        """Oturum epoch'una göre anlık adım numarasını döner."""
        current_time = time.monotonic()
        if current_time < self.start_time:
            return -1
        return int((current_time - self.start_time) // self.interval)

    def get_time_to_next_step(self) -> float:
        """Bir sonraki adıma kalan saniye miktarını döner."""
        current_time = time.monotonic()
        if current_time < self.start_time:
            return self.start_time - current_time
        elapsed = (current_time - self.start_time) % self.interval
        return self.interval - elapsed
