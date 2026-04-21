import hashlib
import json

class ProtocolDecisionLogic:
    def __init__(self):
        self.rtt_history = []
        self.loss_count = 0
        self.total_count = 0
        self.window_size = 50

    def record_packet(self, rtt: float = None, lost: bool = False):
        """Gerçek uygulama katmanı RTT ölçümünü veya paket kaybını kaydeder."""
        self.total_count += 1
        if lost:
            self.loss_count += 1
        elif rtt is not None:
            self.rtt_history.append(rtt)
            if len(self.rtt_history) > self.window_size:
                self.rtt_history.pop(0)

    def _get_network_state(self) -> str:
        avg_latency = sum(self.rtt_history) / len(self.rtt_history) if self.rtt_history else 0.0
        # Kayıp oranının güncel durumu yansıtması için toplam sayıyı baz al
        loss_rate = (self.loss_count / self.total_count) if self.total_count > 0 else 0.0

        # SDD 5.2: >200ms veya >%5 kayıp => CONGESTED (SIKIŞIK)
        if avg_latency > 0.200 or loss_rate > 0.05:
            return "CONGESTED"
        return "STABLE"

    def determine_protocol(self, bootstrap_params: dict, step: int, force_mode: str) -> str:
        """
        Deterministik protokolü hesaplar.
        force_mode 'TCP', 'UDP' veya 'AUTO' olabilir.
        """
        if force_mode in ("TCP", "UDP"):
            return force_mode
            
        state = self._get_network_state()
        
        # SDD 5.2 Ağırlıklandırması
        if state == "STABLE":
            udp_threshold = 50
        else:
            udp_threshold = 20 # 20'den büyükse UDP seçileceği için UDP çıkma ihtimali %80
            
        param_str = json.dumps(bootstrap_params, sort_keys=True)
        m = hashlib.sha256(f"{param_str}_{step}".encode('utf-8'))
        random_val = int(m.hexdigest(), 16) % 100

        if random_val >= udp_threshold:
            return "UDP"
        return "TCP"
