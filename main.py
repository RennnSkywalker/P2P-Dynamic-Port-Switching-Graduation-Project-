import argparse
import sys
import os
import time
import threading
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
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--min-port", type=int, default=20000)
    parser.add_argument("--max-port", type=int, default=30000)
    parser.add_argument("--web-port", type=int, default=8080, help="Web UI sunucu portu")
    return parser.parse_args()

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

    logger.info("Initiating automatic Out-of-band Public Key Exchange via Discovery Port...")

    is_dialer = (args.peer_id == 1)
    bm = BootstrapManager(args.target_ip, args.peer_id, priv_key_path, pub_key_path, peer_pub_key_path)
    
    bootstrap_params = None
    if is_dialer:
        # Arayıcı (Dialer) deterministik parametreleri oluşturur
        bootstrap_params = {
            "timing_interval": args.interval,
            "port_range_min": args.min_port,
            "port_range_max": args.max_port,
            "bootstrap_input_count": 5, 
            "epoch": time.time() + 5.0 # Her iki tarafın tam senkronize olması için 5sn beklet
        }
    
    try:
        bootstrap_params = bm.run_bootstrap_flow(is_dialer, bootstrap_params)
        logger.info(f"Bootstrap complete. Params: {bootstrap_params}")
    except Exception as e:
        logger.critical(f"Bootstrap sequence failed: {e}")
        return

    # Senkronizasyon (Epoch) başlangıç noktasını bekle
    wait_time = bootstrap_params['epoch'] - time.time()
    if wait_time > 0:
        logger.info(f"Waiting {wait_time:.2f} seconds for synchronization epoch...")
        time.sleep(wait_time)

    # Çekirdek uygulama mantığını başlat
    comm = CommController(args.target_ip, args.peer_id, bootstrap_params, logger, mode=args.mode)
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
            except:
                pass
            time.sleep(0.5)

    threading.Thread(target=print_messages, daemon=True).start()

    logger.info("Chat Interface Started. Ready for Input.")
    try:
        while True:
            line = input()
            if line.strip().lower() in ("exit", "quit"):
                break
            if line.strip():
                comm.send_message(line.strip())
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down...")
        comm.stop()

if __name__ == "__main__":
    main()
