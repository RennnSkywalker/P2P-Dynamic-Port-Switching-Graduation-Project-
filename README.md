# P2P Dinamik Port Değiştirme (P2P Dynamic Port Switching)

Bu proje, C yerine Python tabanlı yeniden yapılandırılarak *SENG 491 - 492 Bitirme Projesi (SDD)* standartlarına uygun olacak şekilde geliştirilmiştir. Proje, iki makinenin belirli aralıklarla (time-step) port değiştirip, ağ kararlılığına göre TCP/UDP arası dinamik geçiş yapmasını (Automatic Protocol Decision) konu alır. Ek olarak RSA şifrelemesi kullanarak asimetrik kilitlerini (Asymmetric Bootstrap) otomatik olarak paylaşırlar.

## Gereksinimler ve Kurulum

Sistemin düzgün çalışabilmesi için **sadece 1 adet** dış kütüphane gerekmektedir. Python'un varsayılan kütüphaneleri dışındaki tek ihtiyacımız `cryptography` modülüdür.

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
Eğer `apt` ile yüklemek istemezseniz ya da hata alırsanız, varsayılan kurulum engellerini aşmak adına pip'ten zorunlu yetki isteyebilirsiniz:
```bash
pip3 install cryptography --break-system-packages
```

## Projeyi İki Ayrı Cihazda Çalıştırma

Kodları iki ayrı cihazda karşılıklı olarak, hiçbir simülasyona yer bırakmadan, saf İnternet Protokolleri (IP) bazında konuşturmak için aşağıdaki adımları uygulayın. *(ÖNEMLİ: Eğer projenin ana klasöründe `keys` adında bir klasör oluşmuşsa, temiz bir başlangıç için onu sildiğinizden emin olun).*

Varsayım olarak cihazlarımız şunlardır:
* **Bilgisayar A (Sizin Bilgisayarınız):** Yerel Ağ İp adresi `192.168.1.10`
* **Bilgisayar B (Karşı Bilgisayar):** Yerel Ağ İp adresi `192.168.1.20`

### 1. Adım: Cihaz A (Node 0 - Dinleyici Lideri)
Proje klasörüne terminalden girin ve **hedef (target) IP olarak Cihaz B'nin adresini** belirterek ana programı başlatın:
```bash
python main.py --peer-id 0 --target-ip 192.168.1.20
```

### 2. Adım: Cihaz B (Node 1 - Arayıcı)
Aynı şekilde diğer bilgisayardan terminale girin ve hedef IP olarak bu sefer **Cihaz A'nın adresini** belirterek bağlayın:
```bash
python main.py --peer-id 1 --target-ip 192.168.1.10
```

### 3. Adım: Otomatik Bağlantı (Asymmetric Handshake)
Sistem çalıştırıldığı an önce bir `keys` klasörü üretir ve kendi gizli/açık RSA anahtarlarını inşa eder. 
Hemen ardından kısa bir saniyeliğine iki bilgisayar birbirlerine TCP `5000` nolu keşif (discovery) portundan ulaşıp, *Açık Anahtarlarını (Public Keys)* fırlatırlar. 
Ardından portu yok edip rastgele ürettikleri diğer güvenli (Örn: `16426`) porta zıplar ve Asimetrik Şifreleme algoritmalarını kurup P2P güvenliğini tescillerler!

Ekranda `Chat Interface Started. Ready for Input.` bildirimini gördükten sonra rahatça mesajlaşabilirsiniz. Ağ gecikmelerini hesaplayan sistem 12 saniyede bir otomatik zıplama yapacaktır.

## İsteğe Bağlı: Tek Cihazda Test Modu (Test Launcher)
Geliştirme esnasında kurulumlarla ve 2 cihazın terminal IP işleriyle uğraşmak istemediğiniz anlarda, sadece sistemin çalışırlığını denemek için **`test_launcher.py`** isimli dosyayı kullanabilirsiniz. İşletim sisteminizin türünü algılar ve iki farklı konsol penceresini Localhost (`127.0.0.1`) üzerinden saniyesinde yan yana bağlar.

```bash
python test_launcher.py
```

## Ağ İzleme Denetimi (Wireshark P2P Tracker)
Uygulamanızı bitirme jürisine veya arkadaşlarınıza *verilerin gerçekten durmadan atlayarak farklı portlardan yönlendirildiğini (Moving Target Defense)* kanıtlamak isterseniz, sohbetlerinizi bitirdikten (veya testteyken) sonra yazdığımız **Wireshark Tracker** destek aracını kullanabilirsiniz:
```bash
python wireshark_tracker.py
```
Bu araç projeniz tarafından üretilen saf logları tarayarak, yalnızca ve yalnızca kullandığınız o spesifik kripto portlarını ayıklar ve yerel olarak bir *Wireshark Display Filter* cümlesi inşa ederek analiz pencerenizi başlatır.
