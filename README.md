# P2P Dinamik Port Değiştirme (P2P Dynamic Port Switching)

Bu proje, C yerine Python tabanlı yeniden yapılandırılarak *SENG 491 - 492 Bitirme Projesi (SDD)* standartlarına uygun olacak şekilde geliştirilmiştir. Proje, iki makinenin belirli aralıklarla (time-step) port değiştirip, ağ kararlılığına göre TCP/UDP arası dinamik geçiş yapmasını (Automatic Protocol Decision) konu alır. Ek olarak RSA şifrelemesi kullanarak asimetrik kilitlerini (Asymmetric Bootstrap) otomatik olarak paylaşırlar.

Ayrıca sisteme gömülü **Canlı Web Arayüzü (Web UI)** sayesinde, terminalden çalışan bu P2P ağ yapısını modern bir tarayıcı penceresinden saniyesi saniyesine izleyebilirsiniz!

## Gereksinimler ve Kurulum

Sistemin düzgün çalışabilmesi için **sadece 1 adet** dış kütüphane gerekmektedir. Python'un varsayılan kütüphaneleri dışındaki tek ihtiyacımız `cryptography` modülüdür. Web sunucusu Python'un kendi standart kütüphaneleriyle (stdlib) yazılmıştır, ekstra bir web framework gerektirmez.

### Windows Kullanıcıları İçin
Terminalde (CMD veya PowerShell) direkt olarak proje dizinindeyken şu komutu yazarak kütüphaneyi kurabilirsiniz:
```bash
pip install cryptography
```

### Linux (Kali / Ubuntu) ve Mac Kullanıcıları İçin
Yeni işletim sistemlerinde PEP-668 sistem korumasına takılmamak adına, Python'un şifreleme kütüphanesini ana paket yöneticinizle yüklemeniz önerilir:
```bash
sudo apt update
sudo apt install python3-cryptography
```

## Projeyi Çalıştırma ve Web Arayüzüne Bağlanma

Aşağıdaki adımları uygulayarak sistemi başlatabilirsiniz. Komutları girdiğiniz an sistem çalışmaya başlar ve **otomatik olarak tarayıcınızda (Örn: Chrome) `http://localhost:8080` adresinde harika bir Web UI sekmesi açar**.

Varsayım olarak cihazlarımız şunlardır:
* **Bilgisayar A (Ubuntu - Dinleyici):** Yerel Ağ İp adresi `192.168.1.22`
* **Bilgisayar B (Kali - Arayıcı):** Yerel Ağ İp adresi `192.168.1.21`

### 1. Adım: Cihaz A (Node 0 - Dinleyici Lideri)
Ubuntu terminalinden girin ve **hedef (target) IP olarak Cihaz B'nin (Kali) adresini** belirterek ana programı başlatın:
```bash
python main.py --peer-id 0 --target-ip 192.168.1.21
```

### 2. Adım: Cihaz B (Node 1 - Arayıcı)
Kali terminalinden girin ve hedef IP olarak bu sefer **Cihaz A'nın (Ubuntu) adresini** belirterek bağlayın:
```bash
python main.py --peer-id 1 --target-ip 192.168.1.22
```

Sistem çalıştırıldığı an terminalin arkasında bir Web UI sunucusu ayağa kalkar. Ekranınızda tarayıcı fırlayacak ve `http://localhost:8080` üzerinden P2P zıplamalarını canlı izleyebileceksiniz.

## Gelişmiş Başlatma Parametreleri (Tüm Seçenekler)

Proje sadece `--peer-id` ve `--target-ip` ile sınırlı değildir. Jüri sunumunda sistemi farklı şekillerde test etmek isterseniz şu parametreleri kullanabilirsiniz:

| Parametre | Ne İşe Yarar? | Varsayılan Değer | Örnek Kullanım |
|---|---|---|---|
| `--peer-id` | Cihazın kimliği (0 Dinleyici, 1 Arayıcı). Zorunludur. | - | `--peer-id 1` |
| `--target-ip` | Karşı bilgisayarın IP adresi. | `127.0.0.1` | `--target-ip 192.168.1.22` |
| `--mode` | Ağ protokolünü zorlar (`AUTO`, `TCP`, `UDP`). | `AUTO` | `--mode UDP` |
| `--interval` | Sistem kaç saniyede bir port değiştirecek? | `10` | `--interval 5` |
| `--min-port` | Zıplanacak portların alt sınırı. | `20000` | `--min-port 40000` |
| `--max-port` | Zıplanacak portların üst sınırı. | `30000` | `--max-port 50000` |
| `--web-port` | Tarayıcıda açılacak Web UI'ın kendi portu. | `8080` | `--web-port 8081` |

**Not:** Bu kuralları sadece Arayıcı'nın (`--peer-id 1`) girmesi yeterlidir. Arayıcı bunları Asimetrik Bootstrap sırasında Dinleyiciye şifreleyerek iletir, Dinleyici kurallara otomatik uyar.

## İsteğe Bağlı: Tek Cihazda Test Modu (Test Launcher)
Geliştirme esnasında kurulumlarla uğraşmak istemediğiniz anlarda **`test_launcher.py`** isimli dosyayı kullanabilirsiniz. Bu araç tek bilgisayarda yan yana iki terminal açar. Portlar çakışmasın diye birinin arayüzünü `http://localhost:8080`, diğerini `http://localhost:8081` adresinden başlatır.
```bash
python test_launcher.py
```

## Ağ İzleme Denetimi (Wireshark P2P Tracker)
Arka planda dönen port trafiğini Wireshark üzerinden kanıtlamak isterseniz, `session.log` dosyasını tarayarak Wireshark'a özel P2P filtresi oluşturan aracımızı çalıştırabilirsiniz:
```bash
python wireshark_tracker.py
```
