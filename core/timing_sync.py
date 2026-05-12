import time


class TimingSync:
    def __init__(self, interval: int):
        self.interval = interval
        # Port/rol geçişlerini yerel monotonic sayaçtan türetmek,
        # cihaz saatleri farklı olsa bile adım takibini kararlı tutar.
        self.start_time = time.monotonic()

    def get_current_step(self) -> int:
        """Yerel başlangıç anına göre anlık adım numarasını döner."""
        current_time = time.monotonic()
        return int((current_time - self.start_time) // self.interval)

    def get_time_to_next_step(self) -> float:
        """Bir sonraki adıma kalan saniye miktarını döner."""
        current_time = time.monotonic()
        elapsed = (current_time - self.start_time) % self.interval
        return self.interval - elapsed
