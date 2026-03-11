# P2P Dynamic Port Switching System 🛡️
**A Moving Target Defense (MTD) Prototype for Secure P2P Communication**

Bu proje, **Çankaya Üniversitesi Yazılım Mühendisliği Bölümü** SENG 491-492 Graduation Project kapsamında geliştirilen bir siber güvenlik prototipidir. Sistemin temel amacı, iki cihaz (peer) arasındaki iletişimi periyodik ve senkronize port değişimleri (port-hopping) ile maskeleyerek saldırganlar için "hareketli bir hedef" (Moving Target Defense) oluşturmaktır.

---

## 🎯 Projenin Amacı (Purpose)
Sistem, iletişim kanallarını sürekli değiştirerek saldırganların hedef servisi bulmasını, analiz etmesini veya kesintiye uğratmasını zorlaştırır. Temel savunma hedefleri:
* **Keşif Saldırılarını Önleme:** Nmap veya Masscan gibi araçlarla yapılan port tarama sonuçlarını anında geçersiz kılar.
* **Fingerprinting Koruması:** Saldırganın servis versiyonu veya protokol detaylarını belirlemesine yetecek kadar uzun süre aynı portta kalmaz.
* **DoS Koruması:** Sabit bir portu hedef alan sel (flooding) saldırılarını, servis o portu terk ettiği için etkisiz hale getirir.
* **Merkeziyetsiz Senkronizasyon:** İki peer, ortak bir gizli anahtar üzerinden merkezi bir sunucuya ihtiyaç duymadan senkronize olur.



---

## 🛠️ Teknik Özellikler (Technical Specs)
* **Dil:** Düşük seviyeli ağ kontrolü ve performans için **C dili**.
* **Standart:** POSIX Socket API (Linux ve macOS uyumlu).
* **Adaptif Protokol:** Ağ gecikmesi (>200ms) veya paket kaybına (>5%) göre TCP ve UDP arasında dinamik geçiş.
* **Özel Paket Formatı:** 10-byte boyutunda optimize edilmiş özel bir header yapısı.
* **Performans:** Kritik hesaplama ve el sıkışma süreçleri için 5ms altı işlem süresi hedefi.



---

## 🏗️ Modüler Mimari (Project Architecture)
Proje, dökümanlarda (SDD) tanımlanan 7 ana modül üzerine kuruludur:
1. **Communication Controller (Module 1):** Socket yaşam döngüsü ve protokol yönetimi.
2. **Protocol Decision Logic (Module 2):** Adaptif geçiş mekanizması.
3. **Port Management (Module 3):** Deterministik port hesaplama.
4. **Timing & Sync Handler (Module 4):** NTP tabanlı zaman adımı yönetimi.
5. **Role Assignment (Module 5):** Listener/Dialer rollerinin dağılımı.
6. **Logging Component (Module 6):** Hibrit günlükleme (Console/File).
7. **Message/Packet Format (Module 7):** Özel header ve serileştirme.



---

## 🚀 Kullanım (Usage)
Proje bir `Makefile` kullanılarak derlenir ve CLI parametreleri ile çalıştırılır.


