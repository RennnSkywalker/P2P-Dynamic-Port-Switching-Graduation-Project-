import argparse
import sys
import os
import time
import threading
import uuid
from core.crypto_utils import CryptoManager
from core.logger import setup_logger
from core.bootstrap import BootstrapManager
from core.comm_controller import CommController
from web_server import start_web_server

def setup_args():
    parser = argparse.ArgumentParser(description="P2P Dynamic Port Switching CLI")
    parser.add_argument("--peer-id", type=int, choices=[0, 1], required=True)
    parser.add_argument("--target-ip", type=str, default="127.0.0.1")
    parser.add_argument("--mode", type=str, choices=["TCP", "UDP", "AUTO"], default="AUTO")
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--min-port", type=int, default=20000)
    parser.add_argument("--max-port", type=int, default=30000)
    parser.add_argument("--web-port", type=int, default=8080, help="Web UI sunucu portu")
    parser.add_argument("--discovery-port", type=int, default=55000,
                        help="Bootstrap keşif portu (varsayılan: 55000). "
                             "Port 5000 Windows/macOS'ta çakışabilir.")
    return parser.parse_args()

def _handle_command(cmd, comm, logger):
    """Terminal komutlarını ayrıştırır ve ilgili işlemi tetikler."""
    parts = cmd.split()
    keyword = parts[0].lower()

    if keyword == "/mode" and len(parts) == 2:
        mode = parts[1].upper()
        if mode in ("TCP", "UDP", "AUTO"):
            step = comm.send_config_update({"mode": mode})
            if step is not None:
                print(f"  ✓ Mod değişikliği '{mode}' step {step}'te uygulanacak")
            else:
                print("  ✗ Geçersiz mod değişikliği")
        else:
            print("  Kullanım: /mode TCP|UDP|AUTO")

    elif keyword == "/set" and len(parts) >= 3:
        if parts[1].lower() == "port-range" and len(parts) == 3:
            try:
                min_s, max_s = parts[2].split("-")
                min_p, max_p = int(min_s), int(max_s)
                if not (1024 <= min_p < max_p <= 65535):
                    print("  ✗ Geçersiz aralık (1024-65535 arası, min < max)")
                    return
                step = comm.send_config_update({"port_range_min": min_p, "port_range_max": max_p})
                if step is not None:
                    print(f"  ✓ Port aralığı {min_p}-{max_p} step {step}'te uygulanacak")
                else:
                    print("  ✗ Geçersiz port aralığı")
            except ValueError:
                print("  Kullanım: /set port-range MIN-MAX (örn: /set port-range 15000-25000)")
        else:
            print("  Kullanım: /set port-range MIN-MAX")

    elif keyword == "/status":
        state = comm.get_state()
        pending = comm.get_pending_config_info()
        print(f"\n  Durum: {'Bağlı ✓' if state['connected'] else 'Bağlantı yok ✗'}")
        print(f"  Port: {state['current_port']} | Peer Port: {state.get('peer_port', '—')}")
        print(f"  Protokol: {state['protocol']} | Rol: {state['role']} | Step: {state['step']}")
        print(f"  Mod: {state.get('mode', comm.mode)}")
        print(f"  Port Aralığı: {comm.bootstrap_params['port_range_min']}-{comm.bootstrap_params['port_range_max']}")
        print(f"  Gecikme: {state['latency_ms']}ms | Kayıp: {state['loss_rate']}%")
        print(f"  Uptime: {state['uptime']}s | Mesaj: {state['msg_count']}")
        if pending:
            print(f"  ⏳ Bekleyen Değişiklikler: {pending}")
        print()

    elif keyword == "/help":
        print("\n  Komutlar:")
        print("  /mode TCP|UDP|AUTO     — Protokol modunu değiştir (her iki tarafta)")
        print("  /set port-range MIN-MAX — Port aralığını değiştir (her iki tarafta)")
        print("  /status                — Anlık sistem durumunu göster")
        print("  /help                  — Bu yardım mesajını göster")
        print("  exit | quit            — Programı kapat\n")

    else:
        print(f"  Bilinmeyen komut: {parts[0]}. /help yazın.")

def main():
    args = setup_args()
    logger = setup_logger()
    
    priv_key_path = f"keys/peer{args.peer_id}_priv.pem"
    pub_key_path = f"keys/peer{args.peer_id}_pub.pem"
    peer_pub_key_path = f"keys/peer{1 - args.peer_id}_pub.pem"
    
    crypto = CryptoManager()
    if not os.path.exists(priv_key_path):
        logger.info(f"Generating RSA Keypair for Peer {args.peer_id}...")
        crypto.generate_keys()
        crypto.save_keys(priv_key_path, pub_key_path)
    else:
        crypto.load_private_key(priv_key_path)
        crypto.sync_public_key_from_private(pub_key_path)
        logger.info(f"Synchronized public key from private key for Peer {args.peer_id}.")

    logger.info("Initiating automatic Out-of-band Public Key Exchange via Discovery Port...")

    is_dialer = (args.peer_id == 1)
    bm = BootstrapManager(args.target_ip, args.peer_id, priv_key_path, pub_key_path, peer_pub_key_path,
                          discovery_port=args.discovery_port)
    
    bootstrap_params = None
    if is_dialer:
        # Arayıcı (Dialer) deterministik parametreleri oluşturur
        bootstrap_params = {
            "timing_interval": args.interval,
            "port_range_min": args.min_port,
            "port_range_max": args.max_port,
            "mode": args.mode,
            "bootstrap_input_count": 5, 
            "start_delay": 5.0,
            "session_seed": str(uuid.uuid4()),
            "session_start_at": time.time() + 5.0
        }
    
    try:
        bootstrap_params, local_start_delay = bm.run_bootstrap_flow(is_dialer, bootstrap_params)
        logger.info(f"Bootstrap complete. Params: {bootstrap_params}")
    except Exception as e:
        logger.critical(f"Bootstrap sequence failed: {e}")
        return

    session_start_monotonic = time.monotonic() + local_start_delay
    logger.info(
        f"Session epoch armed. Local synchronized start in {local_start_delay:.2f} seconds."
    )

    # Çekirdek uygulama mantığını başlat
    # Mod: Dialer'ın bootstrap_params içinde gönderdiği değeri kullan
    effective_mode = bootstrap_params.get("mode", args.mode)

    comm = CommController(
        args.target_ip,
        args.peer_id,
        bootstrap_params,
        logger,
        mode=effective_mode,
        session_start_monotonic=session_start_monotonic,
    )
    comm.start()

    # Web UI sunucusunu başlat
    try:
        start_web_server(comm, args.peer_id, bootstrap_params, port=args.web_port)
        url = f"http://localhost:{args.web_port}"
        logger.info(f"Web UI started at {url}")
        
        # Tarayıcıyı otomatik aç
        import webbrowser
        webbrowser.open(url)
    except Exception as e:
        logger.warning(f"Web UI could not start: {e}")

    def print_messages():
        while True:
            try:
                msgs = comm.fetch_messages()
                for m in msgs:
                    # Yeşil renk için ANSI kodlaması
                    print(f"\n\033[92m[Peer {1 - args.peer_id}]: {m}\033[0m")
            except Exception as e:
                logger.debug(f"Message printer loop encountered an error: {e}")
            time.sleep(0.5)

    threading.Thread(target=print_messages, daemon=True).start()

    logger.info("Chat Interface Started. Ready for Input. Type /help for commands.")
    try:
        while True:
            line = input()
            stripped = line.strip()
            if stripped.lower() in ("exit", "quit"):
                break
            if stripped.startswith("/"):
                _handle_command(stripped, comm, logger)
            elif stripped:
                comm.send_message(stripped)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down interactive session.")
    finally:
        logger.info("Shutting down...")
        comm.stop()

if __name__ == "__main__":
    main()
