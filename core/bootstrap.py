import socket
import json
import logging
import random
import time
import os
import base64
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
            
        self.discovery_port = discovery_port
        self.connect_timeout = 2.0
        self.accept_timeout = 10.0
        self.recv_timeout = 10.0

    @staticmethod
    def _recv_exact(sock, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            piece = sock.recv(length - len(chunks))
            if not piece:
                raise ConnectionError("Socket closed while reading bootstrap frame")
            chunks.extend(piece)
        return bytes(chunks)

    def _recv_framed(self, sock) -> bytes:
        length = int.from_bytes(self._recv_exact(sock, 4), 'big')
        return self._recv_exact(sock, length)

    @staticmethod
    def _send_framed(sock, payload: bytes):
        sock.sendall(len(payload).to_bytes(4, 'big') + payload)

    def run_stage1_listener(self) -> int:
        """Aşama 1: Basit klasik soket. Arayıcıdan dinamik başlangıç portunu teslim alır."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(self.accept_timeout)
        s.bind(('0.0.0.0', self.discovery_port))
        s.listen(1)
        logger.info(f"[BOOTSTRAP Stage 1] Listening on Discovery Port {self.discovery_port}")
        
        try:
            conn, addr = s.accept()
        except socket.timeout as exc:
            s.close()
            raise TimeoutError("Timed out waiting for stage 1 dialer connection") from exc

        conn.settimeout(self.recv_timeout)
        data = self._recv_framed(conn)
        
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
        self._send_framed(conn, reply_payload)
        
        conn.close()
        s.close()
        return next_port

    def run_stage1_dialer(self) -> int:
        """Aşama 1: Arayıcı tarafı dinamik başlangıç portunu iletir."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.connect_timeout)
        next_port = random.randint(10000, 20000)
        connected = False
        
        # Listener'in açılması biraz sürebileceğinden Dialer için yeniden deneme mantığı
        for _ in range(5):
            try:
                s.connect((self.target_ip, self.discovery_port))
                connected = True
                break
            except OSError as e:
                logger.warning(
                    f"[BOOTSTRAP Stage 1] Discovery connect attempt failed for {self.target_ip}:{self.discovery_port}: {e}"
                )
                try:
                    s.close()
                except OSError:
                    logger.debug("[BOOTSTRAP Stage 1] Failed to close discovery socket after connect error.")
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(self.connect_timeout)
                time.sleep(1)

        if not connected:
            s.close()
            raise ConnectionError(
                f"Could not connect to discovery port {self.discovery_port} on {self.target_ip}"
            )
                
        with open(self.pub_key_path, "r") as f:
            my_pub_key = f.read()
            
        payload = json.dumps({'next_port': next_port, 'pub_key': my_pub_key}).encode('utf-8')
        self._send_framed(s, payload)
        
        s.settimeout(self.recv_timeout)
        data = self._recv_framed(s)
        reply_info = json.loads(data.decode('utf-8'))
        peer_pub_key_str = reply_info['pub_key']
        
        with open(self.peer_pub_key_path, "w") as f:
            f.write(peer_pub_key_str)
        self.peer_public_key = self.crypto.load_public_key(self.peer_pub_key_path)
        
        s.close()
        logger.info(f"[BOOTSTRAP Stage 1] Sent dynamic bootstrap port: {next_port} and received Listener's Public Key")
        return next_port

    def run_stage2_listener(self, port: int) -> dict:
        """Aşama 2: Dinamik portu dinler, RSA ile şifrelenmiş veri paketini alır ve çözer."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(self.accept_timeout)
        # Güvenli bir şekilde bağla (Bind)
        bind_deadline = time.monotonic() + 10.0
        while True:
            try:
                s.bind(('0.0.0.0', port))
                break
            except OSError as e:
                if time.monotonic() >= bind_deadline:
                    s.close()
                    raise TimeoutError(f"Timed out binding encrypted bootstrap port {port}") from e
                logger.error(f"Bind failed on {port}: {e}, retrying...")
                time.sleep(1)
        
        s.listen(1)
        logger.info(f"[BOOTSTRAP Stage 2] Listening for encrypted parameters on {port}...")
        
        try:
            conn, addr = s.accept()
        except socket.timeout as exc:
            s.close()
            raise TimeoutError(f"Timed out waiting for encrypted bootstrap payload on {port}") from exc

        conn.settimeout(self.recv_timeout)
        encrypted_data = self._recv_framed(conn)
        conn.sendall(b"ACK")
        conn.close()
        s.close()

        payload = json.loads(encrypted_data.decode('utf-8'))
        decrypted_data = self.crypto.decrypt_hybrid(
            base64.b64decode(payload['encrypted_key']),
            base64.b64decode(payload['nonce']),
            base64.b64decode(payload['ciphertext'])
        )
        bootstrap_params = json.loads(decrypted_data.decode('utf-8'))
        logger.info(f"[BOOTSTRAP Stage 2] Successfully decrypted Bootstrap Parameters.")
        local_start_delay = max(0.0, float(bootstrap_params.get("start_delay", 0.0)))
        return bootstrap_params, local_start_delay

    def run_stage2_dialer(self, port: int, bootstrap_params: dict):
        """Aşama 2: Parametreleri şifreler ve dinamik port üzerinden gönderir."""
        bootstrap_params["session_start_at"] = time.time() + max(
            0.0, float(bootstrap_params.get("start_delay", 0.0))
        )
        plaintext = json.dumps(bootstrap_params).encode('utf-8')
        encrypted_payload = self.crypto.encrypt_hybrid(plaintext, self.peer_public_key)
        encrypted_data = json.dumps({
            'encrypted_key': base64.b64encode(encrypted_payload['encrypted_key']).decode('ascii'),
            'nonce': base64.b64encode(encrypted_payload['nonce']).decode('ascii'),
            'ciphertext': base64.b64encode(encrypted_payload['ciphertext']).decode('ascii')
        }).encode('utf-8')
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.connect_timeout)
        connected = False
        for _ in range(5):
            try:
                s.connect((self.target_ip, port))
                connected = True
                break
            except OSError as e:
                logger.warning(
                    f"[BOOTSTRAP Stage 2] Encrypted channel connect attempt failed for {self.target_ip}:{port}: {e}"
                )
                try:
                    s.close()
                except OSError:
                    logger.debug("[BOOTSTRAP Stage 2] Failed to close encrypted bootstrap socket after connect error.")
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(self.connect_timeout)
                time.sleep(1)

        if not connected:
            s.close()
            raise ConnectionError(
                f"Could not connect to encrypted bootstrap port {port} on {self.target_ip}"
            )

        send_started_at = time.monotonic()
        self._send_framed(s, encrypted_data)
        s.settimeout(self.recv_timeout)
        ack = s.recv(1024)
        ack_received_at = time.monotonic()
        s.close()

        local_start_delay = max(0.0, float(bootstrap_params.get("start_delay", 0.0)))
        if ack == b"ACK":
            rtt = ack_received_at - send_started_at
            local_start_delay = max(0.0, local_start_delay - (rtt / 2.0))
            logger.info("[BOOTSTRAP Stage 2] Payload acknowledged by Listener.")
        else:
            logger.warning(f"[BOOTSTRAP Stage 2] Unexpected ACK payload: {ack!r}")
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
