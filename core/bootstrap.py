import socket
import json
import logging
import random
import time
import os
from .crypto_utils import CryptoManager

logger = logging.getLogger("SystemLog")

class BootstrapManager:
    def __init__(self, target_ip, peer_id, priv_key_path, pub_key_path, peer_pub_key_path, discovery_port=55000):
        self.target_ip = target_ip
        # Bu başlangıç bağlamında 0=Dinleyici (Listener), 1=Arayıcı (Dialer) olarak atanır
        # Bizim bağlamımızda, Node 0 Başlangıç aşamasında Dinleyici, Node 1 Arayıcı rolündedir
        self.peer_id = peer_id
        self.pub_key_path = pub_key_path
        self.peer_pub_key_path = peer_pub_key_path

        self.crypto = CryptoManager()
        self.crypto.load_private_key(priv_key_path)
        
        if os.path.exists(peer_pub_key_path):
            self.peer_public_key = self.crypto.load_public_key(peer_pub_key_path)
        else:
            self.peer_public_key = None

        # macOS 12+ varsayılan olarak port 5000'i AirPlay Receiver için kullanır.
        # Bu nedenle varsayılan keşif portu 55000 olarak ayarlandı.
        self.discovery_port = discovery_port

    def run_stage1_listener(self) -> int:
        """Aşama 1: Basit klasik soket. Arayıcıdan dinamik başlangıç portunu teslim alır."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # macOS / Linux üzerinde hızlı yeniden bağlama için SO_REUSEPORT ekle
        if hasattr(socket, 'SO_REUSEPORT'):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        conn = None
        try:
            s.bind(('0.0.0.0', self.discovery_port))
            s.listen(1)
            s.settimeout(120)  # Dialer 120 saniye içinde bağlanmazsa hata ver
            logger.info(f"[BOOTSTRAP Stage 1] Listening on Discovery Port {self.discovery_port}")

            conn, addr = s.accept()
            data = conn.recv(8192)

            port_info = json.loads(data.decode('utf-8'))
            next_port = port_info['next_port']
            peer_pub_key_str = port_info['pub_key']
            logger.info(f"[BOOTSTRAP Stage 1] Received dynamic bootstrap port: {next_port} and Dialer's Public Key")

            with open(self.peer_pub_key_path, "w") as f:
                f.write(peer_pub_key_str)
            self.peer_public_key = self.crypto.load_public_key(self.peer_pub_key_path)

            with open(self.pub_key_path, "r") as f:
                my_pub_key = f.read()

            reply_payload = json.dumps({'pub_key': my_pub_key}).encode('utf-8')
            conn.sendall(reply_payload)
            return next_port
        finally:
            if conn:
                try: conn.close()
                except: pass
            try: s.close()
            except: pass

    def run_stage1_dialer(self) -> int:
        """Aşama 1: Arayıcı tarafı dinamik başlangıç portunu iletir."""
        next_port = random.randint(10000, 20000)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Listener'in açılması biraz sürebileceğinden Dialer için yeniden deneme mantığı
            connected = False
            for attempt in range(10):
                try:
                    s.connect((self.target_ip, self.discovery_port))
                    connected = True
                    break
                except Exception:
                    logger.info(f"[BOOTSTRAP Stage 1] Waiting for Listener... (attempt {attempt+1}/10)")
                    time.sleep(1)
                    # Her denemede yeni soket aç (macOS bağlantı hatası sonrası soketi geçersiz sayar)
                    s.close()
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            if not connected:
                raise ConnectionError(f"Could not connect to Listener at {self.target_ip}:{self.discovery_port} after 10 attempts.")

            with open(self.pub_key_path, "r") as f:
                my_pub_key = f.read()

            payload = json.dumps({'next_port': next_port, 'pub_key': my_pub_key}).encode('utf-8')
            s.sendall(payload)

            data = s.recv(8192)
            reply_info = json.loads(data.decode('utf-8'))
            peer_pub_key_str = reply_info['pub_key']

            with open(self.peer_pub_key_path, "w") as f:
                f.write(peer_pub_key_str)
            self.peer_public_key = self.crypto.load_public_key(self.peer_pub_key_path)

            logger.info(f"[BOOTSTRAP Stage 1] Sent dynamic bootstrap port: {next_port} and received Listener's Public Key")
            return next_port
        finally:
            try: s.close()
            except: pass
        return next_port

    def run_stage2_listener(self, port: int) -> dict:
        """Aşama 2: Dinamik portu dinler, RSA ile şifrelenmiş veri paketini alır ve çözer."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Güvenli bir şekilde bağla (Bind)
        while True:
            try:
                s.bind(('0.0.0.0', port))
                break
            except Exception as e:
                logger.error(f"Bind failed on {port}: {e}, retrying...")
                time.sleep(1)
        
        s.listen(1)
        logger.info(f"[BOOTSTRAP Stage 2] Listening for encrypted parameters on {port}...")
        
        conn, addr = s.accept()
        encrypted_data = conn.recv(4096)
        conn.sendall(b"ACK")
        conn.close()
        s.close()
        
        decrypted_data = self.crypto.decrypt_data(encrypted_data)
        bootstrap_params = json.loads(decrypted_data.decode('utf-8'))
        logger.info(f"[BOOTSTRAP Stage 2] Successfully decrypted Bootstrap Parameters.")
        local_start_delay = max(0.0, float(bootstrap_params.get("start_delay", 0.0)))
        return bootstrap_params, local_start_delay

    def run_stage2_dialer(self, port: int, bootstrap_params: dict):
        """Aşama 2: Parametreleri şifreler ve dinamik port üzerinden gönderir."""
        plaintext = json.dumps(bootstrap_params).encode('utf-8')
        encrypted_data = self.crypto.encrypt_data(plaintext, self.peer_public_key)
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        for _ in range(5):
            try:
                s.connect((self.target_ip, port))
                break
            except Exception:
                time.sleep(1)

        send_started_at = time.monotonic()
        s.sendall(encrypted_data)
        ack = s.recv(1024)
        ack_received_at = time.monotonic()
        s.close()

        local_start_delay = max(0.0, float(bootstrap_params.get("start_delay", 0.0)))
        if ack == b"ACK":
            rtt = ack_received_at - send_started_at
            local_start_delay = max(0.0, local_start_delay - (rtt / 2.0))
            logger.info("[BOOTSTRAP Stage 2] Payload acknowledged by Listener.")
        else:
            logger.warning("[BOOTSTRAP Stage 2] Failed to get ACK.")
        return local_start_delay

    def run_bootstrap_flow(self, is_dialer: bool, bootstrap_params: dict = None) -> dict:
        """İki aşamalı tam başlangıç (bootstrap) protokolünü çalıştırır."""
        if is_dialer:
            if not bootstrap_params:
                raise ValueError("Dialer must provide bootstrap_params.")
            dyn_port = self.run_stage1_dialer()
            time.sleep(0.5) # Listener'ın bağlanmasını garantile
            local_start_delay = self.run_stage2_dialer(dyn_port, bootstrap_params)
            return bootstrap_params, local_start_delay
        else:
            dyn_port = self.run_stage1_listener()
            params, local_start_delay = self.run_stage2_listener(dyn_port)
            return params, local_start_delay
