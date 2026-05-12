import os
import re
import platform
import subprocess
import shutil

def parse_ports_from_log(log_path):
    if not os.path.exists(log_path):
        print(f"[HATA] {log_path} bulunamadı.")
        return []
        
    ports = set([5000]) # Keşif (Discovery) portu olan 5000 her zaman eklenir.
    
    with open(log_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Log dosyasında geçen tüm potansiyel port kullanımlarını regex ile ayıkla
    target_ports = re.findall(r'Target Port:\s*(\d+)', content)
    bootstrap_ports = re.findall(r'dynamic bootstrap port:\s*(\d+)', content)
    rehandshake_ports = re.findall(r'on port\s*(\d+)', content)
    encrypted_listen = re.findall(r'encrypted parameters on\s*(\d+)', content)
    
    for p in target_ports + bootstrap_ports + rehandshake_ports + encrypted_listen:
        try:
            ports.add(int(p))
        except ValueError:
            pass
            
    return sorted(list(ports))

def main():
    print("=== P2P Wireshark Analiz ve Takip Aracı ===")
    log_path = "session.log"
    
    print(f"[*] '{log_path}' dosyası taranıyor...")
    ports = parse_ports_from_log(log_path)
    
    if not ports:
        print("Sistemde henüz kullanılmış bir port bulunamadı. Sistem hiç log üretmemiş olabilir.")
        return
        
    print(f"[+] Tespit Edilen Kullanılmış Portlar: {ports}")
    
    # Wireshark Görüntüleme Filtresi (Display Filter) oluşturma
    # Eski Wireshark sürümleriyle de %100 uyumlu olması için "tcp.port==X or udp.port==Y" formatı
    tcp_filters = " or ".join([f"tcp.port=={p}" for p in ports])
    udp_filters = " or ".join([f"udp.port=={p}" for p in ports])
    filter_str = f"({tcp_filters}) or ({udp_filters})"
    
    print("\n[+] Özel Wireshark Filtresi Hazırlandı:")
    print("-" * 70)
    print(filter_str)
    print("-" * 70)
    
    print("\n[*] Wireshark otomatik olarak başlatılmaya çalışılıyor...")
    
    system = platform.system()
    try:
        if system == "Windows":
            # Windows'ta Wireshark'ın genel kurulum konumlarını ara
            wireshark_paths = [
                r"C:\Program Files\Wireshark\Wireshark.exe",
                r"C:\Program Files (x86)\Wireshark\Wireshark.exe"
            ]
            ws_path = None
            for p in wireshark_paths:
                if os.path.exists(p):
                    ws_path = p
                    break
            
            if ws_path:
                # Sadece filtreyi uygulayarak başlatıyoruz. (Interface seçimi vs. Wireshark arayüzünden yapılır)
                subprocess.Popen([ws_path, "-Y", filter_str])
                print("[+] Wireshark başarıyla başlatıldı ve P2P filtresi uygulandı!")
            else:
                print("[-] Wireshark 'Program Files' içinde bulunamadı.")
                print("Lütfen ekranda yazan özel filtreyi manuel olarak açtığınız Wireshark paneline yapıştırın.")
                
        elif system in ["Linux", "Darwin"]:
            if shutil.which("wireshark"):
                subprocess.Popen(["wireshark", "-Y", filter_str])
                print("[+] Wireshark başarıyla başlatıldı ve P2P filtresi uygulandı!")
            else:
                print("[-] Sisteminizde 'wireshark' yüklü olarak algılanamadı.")
                print("Lütfen ekranda yazan özel filtreyi kopyalayıp çalışan makinenizdeki ağ analiz aracına yapıştırın.")
    except Exception as e:
        print(f"[HATA] Wireshark başlatılırken bir hata meydana geldi: {e}")

if __name__ == "__main__":
    main()
