/**
 * @file comm_controller.h
 * @brief Modül 1 — İletişim Denetleyici (Genel API)
 *
 * İletişim Denetleyici, tüm Taşıma Katmanı (Transport Layer) işlemlerinin
 * merkezi yöneticisidir. Yönettiği işlemler:
 *   - TCP ve UDP soket yaşam döngüsü (başlatma, bağlama, bağlanma, kapatma)
 *   - Protokol durum makinesi (IDLE → CONNECTED → SWITCHING → CONNECTED)
 *   - Devirme (Handover) mantığı: giden kuyruğu boşaltır, eski soketi kapatır,
 *     yeni porta yeniden bağlanır, ardından arabellekteki mesajları gönderir
 *   - Uyarlamalı (Otomatik) mod: SDD §5.2 / SRS FR8'de tanımlanan ağ sağlığı
 *     eşiklerine göre TCP↔UDP arasında geçiş yapar
 *
 * SDD Referansı: §4.1 (Modül 1), §5.1 (İletişim Denetleyici Akışı)
 * SRS Referansı: FR8, §3.5.1 (Performans < 5 ms), §3.5.4 (Güvenlik)
 *
 * Şu modüllerle bağlantı kurulacak şekilde tasarlanmıştır:
 *   - port_mgmt.c  (Modül 3) — cc_extern_get_target_port() sağlar
 *   - timing_sync.c (Modül 4) — cc_extern_get_time_step() sağlar
 *
 * Uyumluluk: C11, POSIX (Linux / macOS).
 */

#ifndef COMM_CONTROLLER_H
#define COMM_CONTROLLER_H

#include <stdint.h>
#include <netinet/in.h>  /* INET_ADDRSTRLEN                              */
#include <pthread.h>     /* pthread_mutex_t                              */
#include "common.h"      /* pkt_header_t, MsgType, PKT_HEADER_SIZE       */

/* =========================================================================
 * Yapılandırma sabitleri
 * ========================================================================= */

/** Bellek içi giden mesaj halka arabelleğinin boyutu (bayt). */
#define SEND_BUF_SIZE        (64u * 1024u)   /* 64 KiB                   */

/** Zorla devirme başlamadan önce giden kuyruğu boşaltmak için maksimum süre (ms). */
#define HANDOVER_DRAIN_TIMEOUT_MS  200u

/** UDP'ye geçişi tetikleyen gecikme eşiği (ms) — Otomatik Mod. */
#define LATENCY_THRESHOLD_MS  200.0

/** UDP'ye geçişi tetikleyen paket kaybı yüzdesi eşiği. */
#define LOSS_THRESHOLD_PCT      5.0

/** TCP dinleme kuyruğu uzunluğu. */
#define TCP_BACKLOG              5

/** Ağ sağlığı metrikleri için EWMA düzleştirme katsayısı (0 < α ≤ 1). */
#define HEALTH_EWMA_ALPHA        0.2

/* =========================================================================
 * Taşıma protokolü numaralandırması
 * ========================================================================= */

/**
 * @brief Etkin taşıma protokolü.
 */
typedef enum {
    PROTO_TCP = 0, /**< Güvenilir, bağlantı odaklı (varsayılan)          */
    PROTO_UDP = 1  /**< Bağlantısız; yüksek ağ tıkanıklığında kullanılır */
} Protocol;

/* =========================================================================
 * Denetleyici durum makinesi
 * ========================================================================= */

/**
 * @brief İletişim denetleyicinin yaşam döngüsü durumu.
 *
 * Geçişler:
 *   IDLE ──(cc_create_tcp/udp_socket)──► CONNECTED
 *   CONNECTED ──(cc_switch_protocol)──► SWITCHING ──► CONNECTED | ERROR
 *   CONNECTED | ERROR ──(cc_teardown)──► IDLE
 */
typedef enum {
    STATE_IDLE       = 0, /**< Soket henüz açılmadı                        */
    STATE_CONNECTED  = 1, /**< Soket açık ve çalışıyor                     */
    STATE_SWITCHING  = 2, /**< Devirme devam ediyor — kuyruk duraklatıldı  */
    STATE_ERROR      = 3  /**< Kurtarılamaz soket hatası                   */
} CommState;

/* =========================================================================
 * Merkezi denetleyici bağlamı
 * ========================================================================= */

/**
 * @brief Bir eşin taşıma katmanına ait tüm durum bilgisi.
 *
 * Yığıtta veya bellekte tahsis edilebilir; kullanmadan önce cc_init()
 * ile başlatılmalıdır. Alt çizgi ile başlayan alanlar özeldir; yalnızca
 * erişim fonksiyonları kullanılmalıdır.
 */
typedef struct {
    /* ---- Soket --------------------------------------------------------- */
    int        active_fd;                   /**< Etkin soket fd'si (-1=yok)       */
    Protocol   active_proto;               /**< PROTO_TCP veya PROTO_UDP          */
    CommState  state;                      /**< Denetleyici durum makinesi        */

    /* ---- Uzak eş adresi ------------------------------------------------ */
    char       target_ip[INET_ADDRSTRLEN]; /**< Eşin IPv4 adresi (noktalı-decimal) */
    uint16_t   target_port;               /**< Güncel hedef port                  */
    int        is_listener;               /**< 1 = bind+accept, 0 = connect       */

    /* ---- Giden halka arabelleği ---------------------------------------- */
    uint8_t    send_buf[SEND_BUF_SIZE];   /**< Dairesel gönderme arabelleği       */
    uint32_t   buf_head;                  /**< Yazma indeksi                      */
    uint32_t   buf_tail;                  /**< Okuma indeksi                      */
    uint32_t   buf_count;                 /**< Arabellekteki bayt sayısı          */

    /* ---- Ağ sağlığı metrikleri (Otomatik Mod) -------------------------- */
    double     avg_latency_ms;            /**< EWMA ortalama gidiş-dönüş süresi (ms) */
    double     packet_loss_pct;           /**< EWMA tahmini kayıp oranı (0–100)      */

    /* ---- Eşzamanlılık -------------------------------------------------- */
    pthread_mutex_t lock; /**< buf_* alanlarını ve durum geçişlerini korur     */
} CommController;

/* =========================================================================
 * Gelecekteki modüller için dışsal bağlantı taslakları
 *
 * Bu semboller, comm_controller.c'nin bağımsız derlenebilmesi için burada
 * bildirilmektedir. port_mgmt.c (Modül 3) ve timing_sync.c (Modül 4)
 * bütünleştirildikten sonra güçlü sembollar tarafından gizlenecektir.
 * ========================================================================= */

/**
 * @brief [Modül 3 taslağı] Dinamik olarak hesaplanmış hedef portu döndürür.
 * @return Hedef port numarası; hata durumunda 0.
 */
extern uint16_t cc_extern_get_target_port(void);

/**
 * @brief [Modül 4 taslağı] Güncel zaman adımı indeksini döndürür.
 * @return Monoton artan adım sayacı.
 */
extern uint64_t cc_extern_get_time_step(void);

/* =========================================================================
 * Genel API — Yaşam Döngüsü
 * ========================================================================= */

/**
 * @brief Bir CommController bağlamını başlatır.
 *
 * Herhangi bir cc_* fonksiyonundan önce bir kez çağrılmalıdır. Tüm alanları
 * sıfırlar, hedef eş IP adresini saklar, is_listener'ı ayarlar ve
 * POSIX mutex'i başlatır.
 *
 * @param cc         Başlatılmamış CommController işaretçisi.
 * @param target_ip  Uzak eşin IPv4 adresi (noktalı-decimal dizisi).
 * @param is_listener 1 ise bind+listen; 0 ise connect gerçekleştirilir.
 * @return  Başarıda 0, hata durumunda -1 (errno ayarlanır).
 */
int cc_init(CommController *cc, const char *target_ip, int is_listener);

/**
 * @brief CommController tarafından tutulan tüm kaynakları serbest bırakır.
 *
 * Etkin soketi kapatır (açıksa), mutex'i yok eder ve yapıyı sıfırlar;
 * böylece yanlışlıkla yeniden kullanımın önüne geçilir.
 *
 * @param cc  Kapatılacak başlatılmış CommController.
 */
void cc_teardown(CommController *cc);

/* =========================================================================
 * Genel API — Soket Yönetimi
 * ========================================================================= */

/**
 * @brief Bir TCP soketi oluşturur ve bağlantı kurar.
 *
 * Dinleyici yolu: socket → setsockopt(SO_REUSEADDR) → bind → listen → accept
 * Arayıcı yolu:   socket → setsockopt(SO_REUSEADDR) → connect
 *
 * Başarıda cc->active_fd ayarlanır ve cc->state = STATE_CONNECTED olur.
 *
 * @param cc    Başlatılmış denetleyici.
 * @param port  Bağlanılacak yerel port (dinleyici) veya bağlantı kurulacak
 *              uzak port (arayıcı).
 * @return Başarıda 0, hata durumunda -1 (errno ayarlanır; ayrıntılı günlük yazılır).
 */
int cc_create_tcp_socket(CommController *cc, uint16_t port);

/**
 * @brief Bir UDP soketi oluşturur ve bağlantı kurar.
 *
 * Dinleyici yolu: socket → bind(port)
 * Arayıcı yolu:   socket → connect(target_ip, port)  [varsayılan uzak adres ayarlanır]
 *
 * Başarıda cc->active_fd ayarlanır ve cc->state = STATE_CONNECTED olur.
 *
 * @param cc    Başlatılmış denetleyici.
 * @param port  Bağlanılacak yerel port (dinleyici) veya connect için uzak port (arayıcı).
 * @return Başarıda 0, hata durumunda -1.
 */
int cc_create_udp_socket(CommController *cc, uint16_t port);

/**
 * @brief Etkin soketi kapatır ve bağlantıyı sonlandırır.
 *
 * shutdown(SHUT_RDWR) ardından close() çağrılır. active_fd == -1 iken
 * çağrılması güvenlidir. active_fd = -1 ve state = STATE_IDLE olarak ayarlar.
 *
 * @param cc  Soketi kapatılacak denetleyici.
 */
void cc_close_socket(CommController *cc);

/* =========================================================================
 * Genel API — Protokol Değiştirme (Devirme / Handover)
 * ========================================================================= */

/**
 * @brief Yeni bir protokole ve/veya porta tam protokol devirme işlemi gerçekleştirir.
 *
 * SDD §5.1 anahtar yürütme sırasını uygular:
 *   1. Mutex al → state = STATE_SWITCHING olarak ayarla
 *   2. Giden kuyruğu boşalt (en fazla HANDOVER_DRAIN_TIMEOUT_MS bekle)
 *   3. Eski soketi kapat
 *   4. new_port üzerinde yeni soket aç (TCP veya UDP)
 *   5. Devirme sırasında arabelleklenen mesajları gönder
 *   6. state = STATE_CONNECTED (veya hata durumunda STATE_ERROR) olarak ayarla
 *   7. Mutex'i serbest bırak
 *
 * @param cc        Değiştirilecek denetleyici.
 * @param new_proto Hedef protokol (PROTO_TCP veya PROTO_UDP).
 * @param new_port  Yeni soket için hedef port.
 * @return Başarıda 0, hata durumunda -1.
 */
int cc_switch_protocol(CommController *cc, Protocol new_proto, uint16_t new_port);

/* =========================================================================
 * Genel API — Gönderme / Alma
 * ========================================================================= */

/**
 * @brief Etkin soket üzerinden çerçevelenmiş bir paket oluşturur ve iletir.
 *
 * Bir pkt_header_t oluşturur (version, msg_type, seq_num, payload_len ayarlanır),
 * ağ bayt sırasına dönüştürür ve hem başlığı hem de yükü yazar.
 *
 * @param cc          Etkin denetleyici.
 * @param type        Mesaj türü (MSG_AUTH, MSG_DATA, MSG_ACK, MSG_ERROR).
 * @param seq         Sıra numarası.
 * @param payload     Yük verisine işaretçi (payload_len == 0 ise NULL olabilir).
 * @param payload_len Yük baytı sayısı.
 * @return Yazılan toplam bayt sayısı (başlık + yük), hata durumunda -1.
 */
int cc_send(CommController *cc, MsgType type, uint32_t seq,
            const uint8_t *payload, uint32_t payload_len);

/**
 * @brief Etkin soketten tek bir çerçevelenmiş paket alır.
 *
 * Önce tam olarak PKT_HEADER_SIZE bayt okur, ardından payload_len bayt okur.
 * payload_len değerini buf_size ve MAX_PAYLOAD_LEN ile doğrular.
 *
 * @param cc          Etkin denetleyici.
 * @param hdr_out     Çıkış: host bayt sırasında çözümlenen başlık.
 * @param payload_buf Yük baytları için çıkış arabelleği.
 * @param buf_size    payload_buf kapasitesi (bayt).
 * @return Alınan toplam bayt (başlık + yük), hata veya bağlantı kesilmesinde -1.
 */
int cc_recv(CommController *cc, pkt_header_t *hdr_out,
            uint8_t *payload_buf, uint32_t buf_size);

/* =========================================================================
 * Genel API — Uyarlamalı Mantık (Otomatik Mod)
 * ========================================================================= */

/**
 * @brief EWMA kullanarak düzleştirilmiş ağ sağlığı metriklerini günceller.
 *
 * Çağıran (veya bir izleme iş parçacığı) anlık ölçümleri sağlar. Denetleyici,
 * geçici ani artışlarda kararsızlığı önlemek amacıyla üstel ağırlıklı hareketli
 * ortalama düzleştirmesi (α = HEALTH_EWMA_ALPHA) uygular.
 *
 * İş parçacığı güvenlidir (mutex içsel olarak alınır).
 *
 * @param cc         Güncellenecek denetleyici.
 * @param latency_ms Anlık gidiş-dönüş gecikmesi (milisaniye).
 * @param loss_pct   Anlık paket kaybı tahmini (0.0–100.0).
 * @return Her zaman 0 (ilerideki hata raporlaması için ayrılmıştır).
 */
int cc_update_network_health(CommController *cc,
                             double latency_ms, double loss_pct);

/**
 * @brief Ağ sağlığını değerlendirir ve gerekiyorsa otomatik protokol geçişini tetikler.
 *
 * SDD §5.2 / SRS FR8'deki Otomatik Mod karar mantığını uygular:
 *
 *   Tıkanık (→ UDP): avg_latency_ms > 200 VEYA packet_loss_pct > 5
 *   Kararlı  (→ TCP): avg_latency_ms ≤ 200 VE  packet_loss_pct ≤ 5
 *
 * Gerekli protokol zaten active_proto ile eşleşiyorsa geçiş yapılmaz.
 *
 * @param cc       Değerlendirilecek denetleyici.
 * @param new_port Geçiş tetiklenirse kullanılacak port.
 * @return  Geçiş tetiklendiyse 1, değişiklik gerekmiyorsa 0, hata durumunda -1.
 */
int cc_check_adaptive_switch(CommController *cc, uint16_t new_port);

#endif /* COMM_CONTROLLER_H */
