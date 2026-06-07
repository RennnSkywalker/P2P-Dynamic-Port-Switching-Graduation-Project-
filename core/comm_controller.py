import socket
import threading
import time
import json
import struct
from .timing_sync import TimingSync
from .port_manager import get_port_pair, get_role_for_peer, get_emergency_port_pair
from .protocol_decision import ProtocolDecisionLogic

class CommController:
    def __init__(
        self,
        target_ip,
        peer_id,
        bootstrap_params,
        logger,
        mode="AUTO",
        session_start_monotonic=None,
    ):
        self.target_ip = target_ip
        self.peer_id = peer_id
        self.bootstrap_params = bootstrap_params
        self.logger = logger
        self.mode = mode
        
        self.timing = TimingSync(
            bootstrap_params['timing_interval'],
            start_monotonic=session_start_monotonic,
        )
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
        self.udp_target = None
        self.next_message_id = 1
        self.pending_messages = {}
        self.received_message_ids = set()
        self.received_message_order = []
        self.udp_ack_timeout = 1.0
        self.max_retries = 3
        self.bytes_sent = 0
        self.bytes_received = 0
        
        # Çalışma zamanı konfigürasyon değişiklikleri
        self.pending_config_changes = {}
        self.config_change_id = 0
        
        # Emergency hop durumu
        self.emergency_hopped = False
        self.on_emergency_port = False
        self.last_success_time = 0.0        # Son başarılı ALIM zamanı (monotonic)
        self.last_ack_received_time = 0.0   # Son başarılı ACK alım zamanı (gönderim sağlığı)
        self.consecutive_send_failures = 0  # Art arda ACK alınamayan UDP mesaj sayısı
        self.current_step_start_time = 0.0  # Mevcut step'in başladığı zaman (emergency fallback)
        self.last_heartbeat_sent = 0.0      # Son gönderilen heartbeat zamanı
        self.emergency_timeout = 3.0        # Sessizlik eşiği (saniye)
        
        # UI durum takibi için ek alanlar
        self.all_messages = []       # Tüm gelen/giden mesaj geçmişi
        self.port_switch_log = []    # Port zıplama kayıtları
        self.port_switch_count = 0
        self.proto_switch_count = 0
        self.prev_protocol = None
        self.current_port = 0
        self.current_peer_port = 0
        self.current_role = ""
        self.start_time = time.time()
        
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
                except OSError as e:
                    self.logger.debug(f"Active socket close during stop failed: {e}")

    def send_message(self, text):
        with self.lock:
            msg_id = f"{self.peer_id}-{self.next_message_id}"
            self.next_message_id += 1
            self.outgoing_queue.append({
                "id": msg_id,
                "text": text,
                "ts": time.time(),
                "type": "DATA"
            })
            self.all_messages.append({
                "dir": "out", "text": text,
                "time": time.strftime("%H:%M:%S"),
                "port": self.current_port,
                "peer_port": self.current_peer_port,
                "proto": self.active_protocol
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
                
                # Emergency hop mantığı
                _now = time.monotonic()
                _no_receive = (
                    self.last_success_time > 0
                    and (_now - self.last_success_time) >= self.emergency_timeout
                )
                _no_ack = (
                    self.consecutive_send_failures >= 3
                    and self.last_ack_received_time > 0
                    and (_now - self.last_ack_received_time) >= self.emergency_timeout
                )

                # FIX: Emergency porta geçildikten sonra o port da bloklanırsa
                # (last_success_time güncellendi ama artık durdu), sistemi tekrar
                # tetikleyebilmek için emergency_hopped bayrağını sıfırla.
                # Bu sayede aynı adımda 2. bir emergency denemesi yapılabilir.
                if (self.emergency_hopped
                        and self.on_emergency_port
                        and self.current_step >= 0
                        and self.last_success_time > 0
                        and (_now - self.last_success_time) >= self.emergency_timeout):
                    with self.lock:
                        self.emergency_hopped = False
                    self.logger.warning(
                        "[EMERGENCY HOP] Emergency port also lost communication. "
                        "Re-triggering on same emergency ports."
                    )

                # Heartbeat Gönderimi (1 saniyede bir)
                # Böylece sistemin 'boşta (idle)' olmasıyla 'bloklu (blocked)' olmasını ayırt edebiliyoruz.
                if self.active_socket and self.last_connected:
                    if _now - self.last_heartbeat_sent >= 1.0:
                        with self.lock:
                            self.outgoing_queue.append({"type": "HEARTBEAT", "ts": time.time()})
                            self.last_heartbeat_sent = _now

                # Port bloklu ve hiç veri alınmamışsa (last_success_time==0),
                # step başlangıcından itibaren emergency_timeout geçtiyse de tetikle.
                _never_had_data = (
                    self.last_success_time == 0.0
                    and self.current_step_start_time > 0
                    and (_now - self.current_step_start_time) >= self.emergency_timeout
                )
                if (not self.emergency_hopped
                        and self.current_step >= 0
                        and self.current_step_start_time > 0
                        and (_no_receive or _no_ack or _never_had_data)):
                    self._handle_emergency_hop(self.current_step)
                
                if self.active_socket and self.last_connected:
                    self._handle_io()
                    self._retry_pending_udp()
            except Exception as e:
                self.logger.error(f"Error in comm loop: {e}")
            time.sleep(0.05)

    def _handle_switch(self, step):
        previous_socket = None
        with self.lock:
            previous_socket = self.active_socket
            self.active_socket = None
            requeued_count = self._requeue_pending_messages_locked()
            self.pending_messages.clear()
            self.udp_target = None
            self.last_connected = False
            self.emergency_hopped = False
            self.on_emergency_port = False
            # Yeni step başlarken tüm zamanlama sayaçlarını sıfırla.
            self.consecutive_send_failures = 0
            self.last_ack_received_time = 0.0
            self.last_success_time = 0.0
            self.current_step_start_time = time.monotonic()

            # Zamanlanmış konfigürasyon değişikliklerini uygula
            if step in self.pending_config_changes:
                changes = self.pending_config_changes.pop(step)
                self._apply_config_changes_locked(changes)

            old_port = self.current_port
            old_peer_port = self.current_peer_port
            
            target_protocol = self.decision_logic.determine_protocol(self.bootstrap_params, step, self.mode)
            role = get_role_for_peer(self.bootstrap_params, step, self.peer_id)
            local_port, peer_port = get_port_pair(self.bootstrap_params, step, self.peer_id)
            
            self.current_port = local_port
            self.current_peer_port = peer_port
            self.current_role = role
            self.active_protocol = target_protocol
            self.port_switch_count += 1
            if self.prev_protocol is not None and self.prev_protocol != target_protocol:
                self.proto_switch_count += 1
            self.prev_protocol = target_protocol
            
            self.port_switch_log.append({
                "time": time.strftime("%H:%M:%S"),
                "from": old_port, "to": local_port,
                "peer_from": old_peer_port, "peer_to": peer_port,
                "proto": target_protocol,
                "type": "green"
            })
            if len(self.port_switch_log) > 30:
                self.port_switch_log.pop(0)

        if previous_socket:
            try:
                if self.active_protocol == "TCP":
                    # OS'in portu (özellikle Dialer için aynı 4-tuple'ı) anında serbest
                    # bırakması ve TIME_WAIT/FIN_WAIT'i atlaması için RST zorla.
                    l_onoff = 1
                    l_linger = 0
                    previous_socket.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', l_onoff, l_linger))
                previous_socket.close()
            except OSError as e:
                self.logger.debug(f"Previous socket close during step switch failed: {e}")

        if requeued_count:
            self.logger.info(
                f"Step switch re-queued {requeued_count} pending UDP message(s) for retransmission."
            )

        self.logger.info(
            f"Interval Reached. Step: {step} | Role: {role} | Proto: {target_protocol} "
            f"| Local Port: {local_port} | Peer Port: {peer_port}"
        )

        established_socket = None
        udp_target = None
        connected = False

        if target_protocol == "TCP":
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(1.0)
            
            if role == "LISTENER":
                while self.running and not connected:
                    if self.timing.get_current_step() > step:
                        break
                    try:
                        sock.bind(('0.0.0.0', local_port))
                        sock.listen(1)
                        conn, addr = sock.accept()
                        sock.close()
                        established_socket = conn
                        established_socket.settimeout(0.2)
                        established_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                        connected = True
                    except OSError as e:
                        self.logger.debug(
                            f"[TCP] Listener handshake retry on local port {local_port} failed at step {step}: {e}"
                        )
                        sock.close()
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.settimeout(0.5)
                        time.sleep(0.1)
            else:
                while self.running and not connected:
                    if self.timing.get_current_step() > step:
                        break
                    try:
                        sock.bind(('0.0.0.0', local_port))
                        sock.connect((self.target_ip, peer_port))
                        established_socket = sock
                        established_socket.settimeout(0.2)
                        connected = True
                    except OSError as e:
                        self.logger.debug(
                            f"[TCP] Dialer handshake retry from local {local_port} to {self.target_ip}:{peer_port} "
                            f"failed at step {step}: {e}"
                        )
                        try:
                            sock.close()
                        except OSError as close_error:
                            self.logger.debug(f"[TCP] Failed to close dialer socket after connect error: {close_error}")
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.settimeout(1.0)
                        time.sleep(0.5)

            if not connected and established_socket is None:
                try:
                    sock.close()
                except OSError as e:
                        self.logger.debug(f"[TCP] Failed to close unconnected handshake socket: {e}")
                self.logger.warning(
                    f"[TCP] Re-handshake did not complete before step advanced. Step: {step} "
                    f"Local Port: {local_port} Peer Port: {peer_port}"
                )
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.2)

            try:
                sock.bind(('0.0.0.0', local_port))
            except OSError as e:
                self.logger.warning(f"[UDP] Bind error on local port {local_port}: {e}")
                sock.close()
            else:
                established_socket = sock
                udp_target = (self.target_ip, peer_port)
                connected = True

        with self.lock:
            if self.current_step != step or not self.running:
                if established_socket:
                    try:
                        established_socket.close()
                    except OSError as e:
                        self.logger.debug(f"Established socket close after stale step failed: {e}")
                return

            self.active_socket = established_socket
            self.last_connected = connected
            self.udp_target = udp_target

        if connected:
            # TCP handshake tamamlanması tek başına gerçek veri alışverişini
            # garanti etmez (port bloklu olsa bile SYN/ACK çalışabilir).
            # last_success_time yalnızca gerçek DATA/ACK alınınca set edilecek.
            # Böylece port bloklu olduğunda emergency_timeout sonra doğru tetiklenir.
            self.logger.info(
                f"[{target_protocol}] Channel ready. Local Port: {local_port} | Peer Port: {peer_port}"
            )
        else:
            self.logger.warning(
                f"[{target_protocol}] Channel setup not established for step {step}. "
                f"Local Port: {local_port} | Peer Port: {peer_port}"
            )

    def _handle_io(self):
        with self.lock:
            # GÖNDERİM KUYRUĞU
            for msg in self.outgoing_queue[:]:
                try:
                    if msg.get("type") == "DATA":
                        msg["step"] = self.current_step
                    payload = json.dumps(msg).encode('utf-8')
                    if self.active_protocol == "TCP":
                        # Uzunluk eklentili TCP
                        length_encoded = len(payload).to_bytes(4, 'big')
                        self.active_socket.sendall(length_encoded + payload)
                        self.bytes_sent += 4 + len(payload)
                    else:
                        self.active_socket.sendto(payload, self.udp_target)
                        self.bytes_sent += len(payload)
                        self.logger.info(f"[UDP] Packet dispatched to {self.target_ip}:{self.udp_target[1]}")
                        if msg.get("type", "DATA") == "DATA":
                            self.pending_messages[msg["id"]] = {
                                "payload": payload,
                                "sent_at": time.monotonic(),
                                "retry_count": 0,
                            }
                    
                    self.outgoing_queue.remove(msg)
                except Exception as e:
                    self.decision_logic.record_packet(lost=True)
                    self.logger.warning(
                        f"Outgoing {msg.get('type', 'UNKNOWN')} message failed on {self.active_protocol} "
                        f"at step {self.current_step}: {e}"
                    )
                    # TCP'de paket kaybı olursa silme, TCP bunu kendisi çözer. Soket koparsa yakala.
                    # Mesajı henüz silme, yeniden deneme için tut.

        # ALIM İŞLEMİ (RECV)
        try:
            if self.active_protocol == "TCP":
                # Gelen verinin uzunluğunu oku
                length_bytes = self._recv_exact(4)
                if len(length_bytes) == 4:
                    length = int.from_bytes(length_bytes, 'big')
                    data = self._recv_exact(length)
                    self.bytes_received += 4 + len(data)
                    self._process_data(data)
            else:
                # UDP
                data, addr = self.active_socket.recvfrom(4096)
                self.bytes_received += len(data)
                self._process_data(data)
        except socket.timeout:
            self.logger.debug(f"{self.active_protocol} receive timed out at step {self.current_step}.")
        except ConnectionError as e:
            self.logger.warning(f"{self.active_protocol} receive connection error at step {self.current_step}: {e}")
        except OSError as e:
            self.logger.warning(f"{self.active_protocol} receive socket error at step {self.current_step}: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected receive error on {self.active_protocol} at step {self.current_step}: {e}")

    def _recv_exact(self, length):
        chunks = bytearray()
        while len(chunks) < length:
            piece = self.active_socket.recv(length - len(chunks))
            if not piece:
                raise ConnectionError("Socket closed while reading frame")
            chunks.extend(piece)
        return bytes(chunks)

    def _retry_pending_udp(self):
        if self.active_protocol != "UDP" or not self.active_socket or not self.udp_target:
            return

        now = time.monotonic()
        with self.lock:
            expired_ids = []
            for msg_id, pending in list(self.pending_messages.items()):
                if now - pending["sent_at"] < self.udp_ack_timeout:
                    continue
                if pending["retry_count"] >= self.max_retries:
                    expired_ids.append(msg_id)
                    continue
                try:
                    self.active_socket.sendto(pending["payload"], self.udp_target)
                    self.bytes_sent += len(pending["payload"])
                    pending["sent_at"] = now
                    pending["retry_count"] += 1
                    self.logger.debug(
                        f"[UDP] Retrying message {msg_id} attempt {pending['retry_count']} to {self.udp_target}."
                    )
                except OSError as e:
                    self.decision_logic.record_packet(lost=True)
                    self.logger.warning(f"[UDP] Retry send failed for message {msg_id}: {e}")

            for msg_id in expired_ids:
                self.pending_messages.pop(msg_id, None)
                self.decision_logic.record_packet(lost=True)
                # Gönderim başarısızlığını say (tek yönlü blok tespiti için)
                self.consecutive_send_failures += 1
                self.logger.warning(f"[UDP] Message {msg_id} expired after {self.max_retries} retry attempts.")

    def _process_data(self, data):
        try:
            msg = json.loads(data.decode('utf-8'))
            msg_type = msg.get("type", "DATA")
            if msg_type == "DATA":
                msg_id = msg.get("id")
                msg_step = msg.get("step")
                if msg_step is not None and msg_step != self.current_step:
                    self.logger.warning(
                        f"Received message for step {msg_step} while local step is {self.current_step}."
                    )
                with self.lock:
                    is_new_message = msg_id not in self.received_message_ids
                    if is_new_message:
                        if msg_id:
                            self.received_message_ids.add(msg_id)
                            self.received_message_order.append(msg_id)
                            if len(self.received_message_order) > 1000:
                                stale_id = self.received_message_order.pop(0)
                                self.received_message_ids.discard(stale_id)
                        self.incoming_queue.append(msg)
                        self.all_messages.append({
                            "dir": "in", "text": msg.get("text", ""),
                            "time": time.strftime("%H:%M:%S"),
                            "port": self.current_port,
                            "peer_port": self.current_peer_port,
                            "proto": self.active_protocol,
                            "step": msg_step
                        })
                    self.last_success_time = time.monotonic()
                
                # Paket onayı (ACK) gönder
                ack = {"type": "ACK", "ts": msg.get("ts", 0), "id": msg_id}
                payload = json.dumps(ack).encode('utf-8')
                
                if self.active_protocol == "TCP":
                    length_encoded = len(payload).to_bytes(4, 'big')
                    self.active_socket.sendall(length_encoded + payload)
                    self.bytes_sent += 4 + len(payload)
                else:
                    self.active_socket.sendto(payload, self.udp_target)
                    self.bytes_sent += len(payload)
                    
            elif msg_type == "ACK":
                # Gecikmeyi hesapla -> RTT (Gidiş-Dönüş Süresi)
                rtt = time.time() - msg.get("ts", time.time())
                msg_id = msg.get("id")
                with self.lock:
                    if msg_id:
                        self.pending_messages.pop(msg_id, None)
                    # Gönderim sağlığını güncelle: ACK aldık demek karşı taraf bizi duyuyor
                    self.last_ack_received_time = time.monotonic()
                    self.consecutive_send_failures = 0
                self.decision_logic.record_packet(rtt=rtt, lost=False)
                self.last_success_time = time.monotonic()

            elif msg_type == "HEARTBEAT":
                with self.lock:
                    self.last_success_time = time.monotonic()

            elif msg_type == "CONFIG_UPDATE":
                changes = msg.get("changes", {})
                effective_step = msg.get("effective_step")
                msg_id = msg.get("id")

                accepted = False
                if effective_step is not None and effective_step > self.current_step:
                    if self._validate_config_changes(changes):
                        with self.lock:
                            self.pending_config_changes[effective_step] = changes
                        accepted = True
                        self.logger.info(
                            f"[CONFIG] Accepted config update {msg_id}: {changes}, "
                            f"effective at step {effective_step}"
                        )
                    else:
                        self.logger.warning(f"[CONFIG] Rejected invalid config update: {changes}")
                else:
                    self.logger.warning(
                        f"[CONFIG] Rejected config update for past/current step: "
                        f"effective={effective_step}, current={self.current_step}"
                    )

                # CONFIG_ACK gönder
                ack = {"type": "CONFIG_ACK", "id": msg_id, "accepted": accepted,
                       "effective_step": effective_step}
                ack_payload = json.dumps(ack).encode('utf-8')
                try:
                    if self.active_protocol == "TCP":
                        length_encoded = len(ack_payload).to_bytes(4, 'big')
                        self.active_socket.sendall(length_encoded + ack_payload)
                        self.bytes_sent += 4 + len(ack_payload)
                    else:
                        self.active_socket.sendto(ack_payload, self.udp_target)
                        self.bytes_sent += len(ack_payload)
                except OSError as e:
                    self.logger.warning(f"[CONFIG] Failed to send CONFIG_ACK: {e}")

            elif msg_type == "CONFIG_ACK":
                msg_id = msg.get("id")
                accepted = msg.get("accepted", False)
                effective_step = msg.get("effective_step")
                with self.lock:
                    if msg_id:
                        self.pending_messages.pop(msg_id, None)
                if accepted:
                    self.logger.info(
                        f"[CONFIG] Peer accepted config update {msg_id}, "
                        f"effective at step {effective_step}"
                    )
                else:
                    self.logger.warning(f"[CONFIG] Peer rejected config update {msg_id}")
                    with self.lock:
                        if effective_step and effective_step in self.pending_config_changes:
                            self.pending_config_changes.pop(effective_step)

        except json.JSONDecodeError as e:
            self.logger.warning(f"Failed to decode incoming payload as JSON: {e}")
        except UnicodeDecodeError as e:
            self.logger.warning(f"Failed to decode incoming payload bytes: {e}")
        except OSError as e:
            self.logger.warning(f"Socket error while processing inbound payload: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected inbound payload processing error: {e}")

    def _requeue_pending_messages_locked(self):
        requeued = 0
        for pending in self.pending_messages.values():
            try:
                msg = json.loads(pending["payload"].decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self.logger.warning(f"Failed to re-queue pending UDP payload: {e}")
                continue
            if msg.get("type") != "DATA":
                continue
            self.outgoing_queue.append(msg)
            requeued += 1
        return requeued

    def send_config_update(self, changes):
        """Çalışma zamanında konfigürasyon değişikliği gönderir.
        Değişiklik current_step + 2'de her iki tarafta da uygulanır."""
        if not self._validate_config_changes(changes):
            self.logger.warning(f"[CONFIG] Invalid config changes rejected locally: {changes}")
            return None

        with self.lock:
            self.config_change_id += 1
            cfg_id = f"cfg-{self.peer_id}-{self.config_change_id}"
            effective_step = max(self.current_step + 2, 0)

            msg = {
                "type": "CONFIG_UPDATE",
                "id": cfg_id,
                "changes": changes,
                "effective_step": effective_step,
                "ts": time.time()
            }
            self.outgoing_queue.append(msg)
            self.pending_config_changes[effective_step] = changes

        self.logger.info(
            f"[CONFIG] Scheduled config update {cfg_id}: {changes}, "
            f"effective at step {effective_step}"
        )
        return effective_step

    def _apply_config_changes_locked(self, changes):
        """Konfigürasyon değişikliklerini uygular (lock altında çağrılmalı)."""
        if "mode" in changes:
            new_mode = changes["mode"]
            if new_mode in ("TCP", "UDP", "AUTO"):
                old_mode = self.mode
                self.mode = new_mode
                self.logger.info(f"[CONFIG] Mode changed: {old_mode} → {new_mode}")

        if "port_range_min" in changes:
            self.bootstrap_params["port_range_min"] = changes["port_range_min"]
        if "port_range_max" in changes:
            self.bootstrap_params["port_range_max"] = changes["port_range_max"]

        if "port_range_min" in changes or "port_range_max" in changes:
            self.logger.info(
                f"[CONFIG] Port range changed: "
                f"{self.bootstrap_params['port_range_min']}-{self.bootstrap_params['port_range_max']}"
            )

    @staticmethod
    def _validate_config_changes(changes):
        """Konfigürasyon değişikliklerini doğrular."""
        if not changes or not isinstance(changes, dict):
            return False

        if "mode" in changes:
            if changes["mode"] not in ("TCP", "UDP", "AUTO"):
                return False

        has_min = "port_range_min" in changes
        has_max = "port_range_max" in changes
        if has_min or has_max:
            min_p = changes.get("port_range_min", 1024)
            max_p = changes.get("port_range_max", 65535)
            if not (1024 <= min_p < max_p <= 65535):
                return False

        return True

    def get_pending_config_info(self):
        """Bekleyen konfigürasyon değişikliklerini döner."""
        with self.lock:
            if not self.pending_config_changes:
                return None
            return {str(k): v for k, v in self.pending_config_changes.items()}

    def _handle_emergency_hop(self, step):
        """İntervalin ortasında iletişim yoksa acil yedek porta geçer."""
        previous_socket = None
        with self.lock:
            previous_socket = self.active_socket
            self.active_socket = None
            self.udp_target = None
            self.last_connected = False
            self.emergency_hopped = True
            self.on_emergency_port = True
            # Zamanlama sayaçlarını sıfırla — emergency porta geçişte eski
            # last_success_time kalırsa koşul bir daha tetiklenemez.
            self.last_success_time = 0.0
            self.last_ack_received_time = 0.0
            self.consecutive_send_failures = 0
            # _never_had_data fallback'i emergency porta geçiş anından başlasın.
            self.current_step_start_time = time.monotonic()

            old_port = self.current_port
            local_port, peer_port = get_emergency_port_pair(
                self.bootstrap_params, step, self.peer_id
            )
            role = get_role_for_peer(self.bootstrap_params, step, self.peer_id)

            self.current_port = local_port
            self.current_peer_port = peer_port

            self.port_switch_log.append({
                "time": time.strftime("%H:%M:%S"),
                "from": old_port, "to": local_port,
                "peer_from": 0, "peer_to": peer_port,
                "proto": self.active_protocol,
                "type": "amber"
            })
            if len(self.port_switch_log) > 30:
                self.port_switch_log.pop(0)

        if previous_socket:
            try:
                if self.active_protocol == "TCP":
                    # OS'in portu anında serbest bırakması için LINGER ile RST zorla.
                    # Bu sayede emergency port tekrar bloklanıp 2. kez aynı emergency
                    # porta re-trigger yapıldığında EADDRINUSE hatası (sessizce takılma) engellenir.
                    l_onoff = 1
                    l_linger = 0
                    previous_socket.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', l_onoff, l_linger))
                previous_socket.close()
            except OSError:
                pass

        if self.peer_id == 0:
            p0_port = local_port
            p1_port = peer_port
        else:
            p0_port = peer_port
            p1_port = local_port

        self.logger.warning(
            f"[EMERGENCY HOP] No communication detected. Switching to emergency ports -> "
            f"Step: {step} | Role: {role} | Proto: {self.active_protocol} | "
            f"Calculated Emergency Ports [Peer 0: {p0_port}, Peer 1: {p1_port}]"
        )

        # Soket kanalını kur
        established_socket = None
        udp_target = None
        connected = False

        if self.active_protocol == "TCP":
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(1.0)

            if role == "LISTENER":
                while self.running and not connected:
                    if self.timing.get_current_step() > step:
                        break
                    try:
                        sock.bind(('0.0.0.0', local_port))
                        sock.listen(1)
                        conn, addr = sock.accept()
                        sock.close()
                        established_socket = conn
                        established_socket.settimeout(0.2)
                        connected = True
                    except OSError:
                        sock.close()
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.settimeout(0.5)
                        time.sleep(0.1)
            else:
                while self.running and not connected:
                    if self.timing.get_current_step() > step:
                        break
                    try:
                        sock.bind(('0.0.0.0', local_port))
                        sock.connect((self.target_ip, peer_port))
                        established_socket = sock
                        established_socket.settimeout(0.2)
                        connected = True
                    except OSError:
                        try:
                            sock.close()
                        except OSError:
                            pass
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.settimeout(1.0)
                        time.sleep(0.3)

            if not connected and established_socket is None:
                try:
                    sock.close()
                except OSError:
                    pass
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.2)
            try:
                sock.bind(('0.0.0.0', local_port))
            except OSError as e:
                self.logger.warning(f"[EMERGENCY HOP] UDP bind error on {local_port}: {e}")
                sock.close()
            else:
                established_socket = sock
                udp_target = (self.target_ip, peer_port)
                connected = True
                # UDP'de bind() başarısı gerçek iletişimi garanti etmez;
                # last_success_time ilk gerçek paket/ACK gelince set edilecek.

        with self.lock:
            if self.current_step != step or not self.running:
                if established_socket:
                    try:
                        established_socket.close()
                    except OSError:
                        pass
                return

            self.active_socket = established_socket
            self.last_connected = connected
            self.udp_target = udp_target

        if connected:
            # TCP'de handshake tamamlandı → karşılıklı iletişim doğrulandı.
            # UDP'de sadece bind() başarılı → gerçek paket gelince set edilecek.
            if self.active_protocol == "TCP":
                self.last_success_time = time.monotonic()
            self.logger.info(
                f"[EMERGENCY HOP] Recovery successful on port {local_port}"
            )
        else:
            # Emergency hop da başarısız oldu. emergency_hopped'ı temizle ki
            # sistem 'asılı' kalmasın; bir sonraki döngüde tekrar deneyebilsin.
            with self.lock:
                self.emergency_hopped = False
                self.on_emergency_port = False
            self.logger.warning(
                f"[EMERGENCY HOP] Recovery failed for step {step}. Will retry."
            )

    def get_state(self):
        """UI için güncel sistem durumunu döner."""
        with self.lock:
            avg_lat = 0
            if self.decision_logic.rtt_history:
                avg_lat = sum(self.decision_logic.rtt_history) / len(self.decision_logic.rtt_history)
            loss_rate = 0
            if self.decision_logic.total_count > 0:
                loss_rate = self.decision_logic.loss_count / self.decision_logic.total_count
            return {
                "connected": self.last_connected,
                "current_port": self.current_port,
                "peer_port": self.current_peer_port,
                "protocol": self.active_protocol,
                "role": self.current_role,
                "step": self.current_step,
                "latency_ms": round(avg_lat * 1000, 1),
                "loss_rate": round(loss_rate * 100, 2),
                "port_switches": self.port_switch_count,
                "proto_switches": self.proto_switch_count,
                "msg_count": len(self.all_messages),
                "uptime": int(time.time() - self.start_time),
                "bytes_sent": self.bytes_sent,
                "bytes_received": self.bytes_received,
                "data_transferred": self.bytes_sent + self.bytes_received,
                "mode": self.mode,
                "pending_config": {str(k): v for k, v in self.pending_config_changes.items()} if self.pending_config_changes else None,
                "on_emergency_port": self.on_emergency_port
            }
