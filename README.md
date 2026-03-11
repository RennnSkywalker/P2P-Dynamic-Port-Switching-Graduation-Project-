# P2P Dynamic Port Switching System 🛡️
**A Moving Target Defense (MTD) Prototype for Secure P2P Communication**

[cite_start]Bu proje, **Çankaya Üniversitesi Yazılım Mühendisliği Bölümü** [cite: 1, 377] [cite_start]SENG 491-492 Graduation Project [cite: 4, 380] kapsamında geliştirilen bir siber güvenlik prototipidir. [cite_start]Temel amacı, iki peer arasındaki iletişimi dinamik ve periyodik port değişimleri (port-hopping) ile koruyarak saldırganların hedef servisi tespit etmesini engellemek ve bir "Moving Target Defense" (MTD) mekanizması sunmaktır[cite: 22, 29].

---

## 🎯 Projenin Amacı (Purpose)
Sistem, iletişim kanallarını sürekli değiştirerek saldırganlar için "hareketli bir hedef" haline gelir. Temel savunma hedefleri şunlardır:
* [cite_start]**Port Tarama ve Keşif Saldırılarını Önleme:** Portlar periyodik olarak değiştiği için tarama sonuçları hızla geçersiz kalır[cite: 65, 66].
* [cite_start]**Servis Fingerprinting Koruması:** Saldırganın derin paket incelemesi yapabileceği stabil bir bağlantı kurmasını engeller[cite: 69].
* [cite_start]**DoS Koruması:** Sabit portlara yönelik uygulama katmanı DoS saldırılarını etkisiz hale getirir[cite: 74].
* [cite_start]**Merkeziyetsiz Senkronizasyon:** İki peer, merkezi bir sunucu olmadan deterministik bir algoritma ile senkronize olur[cite: 31, 399].



---

## 🛠️ Teknik Özellikler (Technical Specs)
* [cite_start]**Dil & Standart:** Düşük seviyeli ağ kontrolü ve performans için **C dili** ve **POSIX Socket API** kullanılmıştır[cite: 323, 397].
* [cite_start]**Desteklenen Platformlar:** Linux ve macOS (Unix-tabanlı sistemler)[cite: 43, 460].
* [cite_start]**Protokol:** TCP ve UDP desteği [cite: 171, 401][cite_start]; ağ gecikmesi (>200ms) veya paket kaybına (>5%) göre otomatik protokol geçişi[cite: 564, 688].
* [cite_start]**Header Yapısı:** Tüm iletişim, dökümanda belirtilen özel **10-byte** header yapısı ile gerçekleştirilir[cite: 664].
* [cite_start]**Performans Hedefi:** Port hesaplama ve kimlik doğrulama işlemleri **5ms** altında tamamlanmalıdır[cite: 336, 337].



---

## 🏗️ Modüler Mimari (Project Architecture)
[cite_start]Sistem, SDD dökümanında tanımlanan ana modüllerden oluşmaktadır[cite: 391]:
1. [cite_start]**Communication Controller (Module 1):** Socket yaşam döngüsü ve protokol yönetimi[cite: 601, 602].
2. [cite_start]**Protocol Decision Logic (Module 2):** Adaptif geçiş mekanizması[cite: 612, 613].
3. [cite_start]**Port Management (Module 3):** Deterministik port hesaplama[cite: 626, 627].
4. [cite_start]**Timing & Sync Handler (Module 4):** NTP tabanlı zaman adımı yönetimi[cite: 635, 637].
5. [cite_start]**Role Assignment (Module 5):** Listener/Dialer rollerinin dağılımı[cite: 648, 649].
6. [cite_start]**Logging Component (Module 6):** Hibrit günlükleme (Console/File)[cite: 654, 656].
7. [cite_start]**Message/Packet Format (Module 7):** Özel header ve serileştirme[cite: 662, 663].

---

## 🚀 Başlatma (Usage)
[cite_start]Sistem, CLI üzerinden konfigüre edilebilir parametrelerle başlatılır[cite: 227].

```bash
# Projeyi derleyin
make

# Peer 0 (Örn: ID 0) olarak başlatın
./dps_system --secretKey "key" --peer-id 0 --interval 30 --port-range 20000-30000 [cite: 228, 480]

# Peer 1 (Örn: ID 1) olarak başlatın
./dps_system --secretKey "key" --peer-id 1 --interval 30 --port-range 20000-30000 [cite: 228, 480]
