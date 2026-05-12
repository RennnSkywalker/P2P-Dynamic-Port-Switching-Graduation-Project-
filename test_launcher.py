import subprocess
import time
import os
import shutil
import sys
import platform

def launch_instance(peer_id, interval, web_port):
    interpreter = sys.executable
    script = "main.py"
    args = ["--peer-id", str(peer_id), "--interval", str(interval), "--mode", "AUTO", "--web-port", str(web_port)]
    system = platform.system()
    
    if system == "Windows":
        subprocess.Popen(
            [interpreter, script] + args,
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
    elif system == "Darwin": # macOS
        # AppleScript kullanarak yeni bir Terminal penceresi açar
        cmd_str = f"cd \\\"{os.getcwd()}\\\" && \\\"{interpreter}\\\" {script} {' '.join(args)}"
        apple_script = f'tell application "Terminal" to do script "{cmd_str}"'
        subprocess.Popen(["osascript", "-e", apple_script])
    elif system == "Linux":
        # Yaygın Linux terminal emülatörlerini dene
        terminals = ["gnome-terminal", "konsole", "xterm", "lxterminal"]
        launched = False
        for term in terminals:
            if shutil.which(term):
                try:
                    if term == "gnome-terminal":
                        subprocess.Popen([term, "--", interpreter, script] + args)
                    else:
                        subprocess.Popen([term, "-e", f"{interpreter} {script} {' '.join(args)}"])
                    launched = True
                    break
                except Exception:
                    continue
        if not launched:
            print("[HATA] Linux'ta desteklenen varsayılan bir terminal emulatorü (gnome-terminal, xterm vb.) bulunamadı.")
            print(f"Lütfen şu komutu elinizle çalıştırın: {interpreter} {script} {' '.join(args)}")
    else:
        print(f"[UYARI] Desteklenmeyen veya tanımlanamayan işletim sistemi: {system}")


def main():
    print("=== P2P Dynamic Port Switching Test Launcher (Cross-Platform) ===")
    print("Test için 'keys' klasörü sıfırlanıyor...")
    if os.path.exists("keys"):
        shutil.rmtree("keys")
    os.makedirs("keys", exist_ok=True)

    print("\n[+] Yeni konsolda Node 0 (Dinleyici rolü) başlatılıyor...")
    launch_instance(0, 12, 8080)

    time.sleep(1) # Kısa bir mola

    print("[+] Yeni konsolda Node 1 (Arayıcı rolü) başlatılıyor...")
    launch_instance(1, 12, 8081)

    print("\n[!] Tüm süreçler başarıyla tetiklendi.")
    print("  Node 0 Web UI: http://localhost:8080")
    print("  Node 1 Web UI: http://localhost:8081")

    print("\n[!] Tüm süreçler başarıyla tetiklendi. Lütfen açılan iki yeni pencereyi kontrol edin.")
    print("Sistemler 2-Aşamalı Başlangıcı yapacak ve her 12 saniyede bir portlarını değiştirecektir.")
    print("Bu çalıştırıcıyı (launcher) kapatabilirsiniz, açılan bağımsız terminaller açık kalmaya devam edecektir.")

if __name__ == "__main__":
    main()
