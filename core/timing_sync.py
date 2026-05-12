import time

class TimingSync:
    def __init__(self, interval: int, epoch: float = 0.0):
        self.interval = interval
        self.epoch = epoch if epoch > 0 else 0.0
        
    def get_current_step(self) -> int:
        """Sistem saatini baz alarak başlangıç zamanına (epoch) göre anlık adım numarasını döner."""
        current_time = time.time()
        return int((current_time - self.epoch) // self.interval)
        
    def get_time_to_next_step(self) -> float:
        """Adımın ilerlemesine kalan saniye miktarını döner."""
        current_time = time.time()
        elapsed = (current_time - self.epoch) % self.interval
        return self.interval - elapsed
