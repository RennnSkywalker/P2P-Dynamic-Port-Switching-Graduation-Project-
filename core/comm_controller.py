import socket
import threading
import time
import json
import traceback
from .timing_sync import TimingSync
from .port_manager import get_target_port, get_role_for_peer
from .protocol_decision import ProtocolDecisionLogic

class CommController:
    def __init__(self, target_ip, peer_id, bootstrap_params, logger, mode="AUTO"):
        self.target_ip = target_ip
        self.peer_id = peer_id
        self.bootstrap_params = bootstrap_params
        self.logger = logger
        self.mode = mode
        
        self.timing = TimingSync(bootstrap_params['timing_interval'], bootstrap_params.get('epoch', 0.0))
        self.decision_logic = ProtocolDecisionLogic()
        
        self.active_socket = None
        self.current_step = -1
        self.running = False
        
        self.outgoing_queue = []
        self.incoming_queue = []
        
        self.lock = threading.Lock()
        self.thread = None
        self.active_protocol = "TCP"
        self.last_connected = False
        
    def start(self):
        self.logger.info("Starting Communication Controller...")
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        with self.lock:
            if self.active_socket:
                try:
                    self.active_socket.close()
                except:
                    pass

    def send_message(self, text):
        with self.lock:
            self.outgoing_queue.append({
                "text": text,
                "ts": time.time(),
                "type": "DATA"
            })

    def fetch_messages(self):
        with self.lock:
            msgs = [m["text"] for m in self.incoming_queue]
            self.incoming_queue.clear()
            return msgs

    def _loop(self):
        while self.running:
            try:
                new_step = self.timing.get_current_step()
                if new_step > self.current_step:
                    self.current_step = new_step
                    self.last_connected = False
                    self._handle_switch(self.current_step)
                
                if self.active_socket and self.last_connected:
                    self._handle_io()
            except Exception as e:
                self.logger.error(f"Error in comm loop: {e}")
            time.sleep(0.05)

    def _handle_switch(self, step):
        with self.lock:
            if self.active_socket:
                try:
                    self.active_socket.close()
                except Exception:
                    pass
                self.active_socket = None

            self.active_protocol = self.decision_logic.determine_protocol(self.bootstrap_params, step, self.mode)
            target_port = get_target_port(self.bootstrap_params, step)
            role = get_role_for_peer(self.bootstrap_params, step, self.peer_id)
            
            self.logger.info(f"Interval Reached. Step: {step} | Role: {role} | Proto: {self.active_protocol} | Target Port: {target_port}")
            
            if self.active_protocol == "TCP":
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.settimeout(1.0)
                
                if role == "LISTENER":
                    success = False
                    while self.running and not success:
                        if self.timing.get_current_step() > step: break
                        try:
                            sock.bind(('0.0.0.0', target_port))
                            sock.listen(1)
                            conn, addr = sock.accept()
                            sock.close()
                            self.active_socket = conn
                            self.active_socket.settimeout(0.2)
                            self.active_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                            success = True
                            self.last_connected = True
                            self.logger.info(f"[TCP] Re-handshake successful on port {target_port}")
                        except Exception:
                            # Windows üzerinde donmaları engellemek için soketi yeniden oluştur
                            sock.close()
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                            sock.settimeout(0.5)
                            time.sleep(0.1)
                else:
                    success = False
                    while self.running and not success:
                        if self.timing.get_current_step() > step: break
                        try:
                            sock.connect((self.target_ip, target_port))
                            self.active_socket = sock
                            self.active_socket.settimeout(0.2)
                            success = True
                            self.last_connected = True
                            self.logger.info(f"[TCP] Re-handshake successful on port {target_port}")
                        except Exception:
                            time.sleep(0.5)
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(0.2)
                if role == "LISTENER":
                    try:
                        sock.bind(('0.0.0.0', target_port))
                    except Exception:
                        pass
                
                self.active_socket = sock
                self.last_connected = True
                self.udp_target = (self.target_ip, target_port)

    def _handle_io(self):
        role = get_role_for_peer(self.bootstrap_params, self.current_step, self.peer_id)
        with self.lock:
            # GÖNDERİM KUYRUĞU
            for msg in self.outgoing_queue[:]:
                try:
                    payload = json.dumps(msg).encode('utf-8')
                    if self.active_protocol == "TCP":
                        # Uzunluk eklentili TCP
                        length_encoded = len(payload).to_bytes(4, 'big')
                        self.active_socket.sendall(length_encoded + payload)
                    else:
                        self.active_socket.sendto(payload, self.udp_target)
                        self.logger.info(f"[UDP] Packet dispatched to {self.target_ip}:{self.udp_target[1]}")
                    
                    self.outgoing_queue.remove(msg)
                except Exception as e:
                    self.decision_logic.record_packet(lost=True)
                    # TCP'de paket kaybı olursa silme, TCP bunu kendisi çözer. Soket koparsa yakala.
                    # Mesajı henüz silme, yeniden deneme için tut.

        # ALIM İŞLEMİ (RECV)
        try:
            if self.active_protocol == "TCP":
                # Gelen verinin uzunluğunu oku
                length_bytes = self.active_socket.recv(4)
                if len(length_bytes) == 4:
                    length = int.from_bytes(length_bytes, 'big')
                    # Eksikse bloke olabilir, basit yaklaşımla doğrudan al
                    data = self.active_socket.recv(length)
                    self._process_data(data)
            else:
                # UDP
                data, addr = self.active_socket.recvfrom(4096)
                self._process_data(data)
        except Exception:
            pass

    def _process_data(self, data):
        try:
            msg = json.loads(data.decode('utf-8'))
            msg_type = msg.get("type", "DATA")
            if msg_type == "DATA":
                with self.lock:
                    self.incoming_queue.append(msg)
                
                # Paket onayı (ACK) gönder
                ack = {"type": "ACK", "ts": msg.get("ts", 0)}
                payload = json.dumps(ack).encode('utf-8')
                
                if self.active_protocol == "TCP":
                    length_encoded = len(payload).to_bytes(4, 'big')
                    self.active_socket.sendall(length_encoded + payload)
                else:
                    self.active_socket.sendto(payload, self.udp_target)
                    
            elif msg_type == "ACK":
                # Gecikmeyi hesapla -> RTT (Gidiş-Dönüş Süresi)
                rtt = time.time() - msg.get("ts", time.time())
                self.decision_logic.record_packet(rtt=rtt, lost=False)
        except Exception:
            pass
