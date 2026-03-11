/**
 * @file comm_controller.c
 * @brief Modül 1 — İletişim Denetleyici (Uygulama)
 *
 * İletişim Denetleyici'nin tam POSIX C11 uygulaması.
 * Genel API ve tasarım gerekçesi için comm_controller.h dosyasına bakın.
 *
 * SDD Referansı: §4.1, §5.1 (İletişim Denetleyici Akışı)
 * SRS Referansı: FR8, §3.5.1 (< 5 ms), §3.5.4, §3.5.5
 *
 * Derleme:
 *   gcc -std=c11 -Wall -Wextra -pedantic -pthread \
 *       comm_controller.c -o libcommctrl.o -c
 *
 * Notlar:
 *   - Tüm soket G/Ç işlemleri engelleyen (blocking) çağrılar kullanır.
 *     Engellemesiz / asenkron G/Ç, genel API değiştirilmeden ileriye
 *     dönük bir sürümde eklenebilir.
 *   - Giden halka arabelleği, devirme mantığının denetlenebilirliğini
 *     korumak amacıyla kasıtlı olarak sade (bayt granüllü) tutulmuştur.
 *   - Modül 3 (port_mgmt.c) ve Modül 4 (timing_sync.c) zayıf sembol
 *     taslakları aracılığıyla başvurulmaktadır; bu sayede bu derleme
 *     birimi bağımsız olarak derlenir.
 */

/* =========================================================================
 * Özellik test makroları — tüm diğer include'lardan önce gelmeli
 * ========================================================================= */
#define _POSIX_C_SOURCE 200809L

/* =========================================================================
 * Kütüphane dahil etmeleri
 * ========================================================================= */
#include "comm_controller.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <time.h>          /* clock_gettime, nanosleep                   */
#include <unistd.h>        /* close(), read(), write()                   */
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <pthread.h>

/* =========================================================================
 * Dışsal modül bağlantısı için zayıf taslaklar (Modül 3 & 4)
 *
 * __attribute__((weak)) ile derlenir; port_mgmt.c ve timing_sync.c
 * bağlandığında güçlü sembolleri öncelik kazanır, bağlayıcı hatası oluşmaz.
 * ========================================================================= */

__attribute__((weak)) uint16_t cc_extern_get_target_port(void)
{
    /* Yer tutucu — Modül 3 bağlanana kadar 0 döndürür. */
    return 0;
}

__attribute__((weak)) uint64_t cc_extern_get_time_step(void)
{
    /* Yer tutucu — Modül 4 bağlanana kadar Unix saniyelerini döndürür. */
    return (uint64_t)time(NULL);
}

/* =========================================================================
 * Dahili günlük yardımcıları
 * ========================================================================= */

/** SDD §3.1.x konsol biçimiyle eşleşen günlük ön eki belirteçleri. */
#define LOG_INFO(fmt, ...)  \
    fprintf(stderr, "[INFO]  " fmt "\n", ##__VA_ARGS__)
#define LOG_WARN(fmt, ...)  \
    fprintf(stderr, "[WARN]  " fmt "\n", ##__VA_ARGS__)
#define LOG_ERROR(fmt, ...) \
    fprintf(stderr, "[ERROR] " fmt "\n", ##__VA_ARGS__)
#define LOG_AUTO(fmt, ...)  \
    fprintf(stderr, "[AUTO]  " fmt "\n", ##__VA_ARGS__)
#define LOG_TCP(fmt, ...)   \
    fprintf(stderr, "[TCP]   " fmt "\n", ##__VA_ARGS__)
#define LOG_UDP(fmt, ...)   \
    fprintf(stderr, "[UDP]   " fmt "\n", ##__VA_ARGS__)

/* =========================================================================
 * Dahili yardımcı — milisaniye cinsinden monoton saat
 * ========================================================================= */

/**
 * @brief Milisaniye cinsinden güncel monoton zamanı döndürür.
 * @return Belirtilmemiş bir referans noktasından itibaren ms (göreli süreler için uygundur).
 */
static uint64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000ULL + (uint64_t)(ts.tv_nsec / 1000000L);
}

/**
 * @brief Belirtilen milisaniye kadar uyutur.
 * @param ms Uyunacak süre (milisaniye, yaklaşık).
 */
static void sleep_ms(unsigned int ms)
{
    struct timespec req;
    req.tv_sec  = (time_t)(ms / 1000u);
    req.tv_nsec = (long)((ms % 1000u) * 1000000L);
    nanosleep(&req, NULL);
}

/* =========================================================================
 * Dahili yardımcı — güvenilir gönderme / alma sarmalayıcıları
 * ========================================================================= */

/**
 * @brief `fd`'ye tam olarak `len` bayt yazar; EINTR durumunda yeniden dener.
 * @return Başarıda `len`, hata veya bağlantı kesilmesinde -1.
 */
static ssize_t write_all(int fd, const void *buf, size_t len)
{
    const uint8_t *ptr = (const uint8_t *)buf;
    size_t remaining   = len;

    while (remaining > 0) {
        ssize_t n = write(fd, ptr, remaining);
        if (n <= 0) {
            if (n < 0 && errno == EINTR) continue;
            return -1;
        }
        ptr       += (size_t)n;
        remaining -= (size_t)n;
    }
    return (ssize_t)len;
}

/**
 * @brief `fd`'den tam olarak `len` bayt okur; EINTR durumunda yeniden dener.
 * @return Başarıda `len`, temiz bağlantı kesilmesinde 0, hata durumunda -1.
 */
static ssize_t read_all(int fd, void *buf, size_t len)
{
    uint8_t *ptr     = (uint8_t *)buf;
    size_t remaining = len;

    while (remaining > 0) {
        ssize_t n = read(fd, ptr, remaining);
        if (n == 0) return 0;  /* EOF / eş bağlantısını kesti */
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        ptr       += (size_t)n;
        remaining -= (size_t)n;
    }
    return (ssize_t)len;
}

/* =========================================================================
 * Dahili yardımcı — halka arabelleği boşaltma
 *
 * cc->send_buf içindeki tüm baytları etkin soket üzerinden gönderir.
 * cc->lock tutularak çağrılmalıdır.
 * ========================================================================= */
static int flush_send_buf(CommController *cc)
{
    if (cc->buf_count == 0 || cc->active_fd < 0)
        return 0;

    /* Halka arabelleği sarabilir; en fazla iki parça halinde işle. */
    uint32_t chunk1_start = cc->buf_tail;
    uint32_t chunk1_len;
    uint32_t chunk2_len;

    if (cc->buf_head > cc->buf_tail) {
        /* Ardışık (sarmıyor) */
        chunk1_len = cc->buf_count;
        chunk2_len = 0;
    } else {
        /* Sarıyor: kuyruktan arabellek sonuna, ardından 0'dan başa */
        chunk1_len = SEND_BUF_SIZE - cc->buf_tail;
        chunk2_len = cc->buf_head;
    }

    if (write_all(cc->active_fd, cc->send_buf + chunk1_start, chunk1_len) < 0) {
        LOG_WARN("flush_send_buf: 1. parçada yazma hatası — %s", strerror(errno));
        return -1;
    }
    if (chunk2_len > 0) {
        if (write_all(cc->active_fd, cc->send_buf, chunk2_len) < 0) {
            LOG_WARN("flush_send_buf: 2. parçada yazma hatası — %s", strerror(errno));
            return -1;
        }
    }

    cc->buf_head  = 0;
    cc->buf_tail  = 0;
    cc->buf_count = 0;
    return 0;
}

/* =========================================================================
 * Genel API — Yaşam Döngüsü
 * ========================================================================= */

int cc_init(CommController *cc, const char *target_ip, int is_listener)
{
    if (!cc || !target_ip) {
        errno = EINVAL;
        return -1;
    }

    memset(cc, 0, sizeof(CommController));
    cc->active_fd   = -1;
    cc->active_proto = PROTO_TCP;
    cc->state        = STATE_IDLE;
    cc->is_listener  = is_listener;

    /* Hedef IP'yi sakla; memset sayesinde strncpy NUL sonlandırmasını garanti eder. */
    strncpy(cc->target_ip, target_ip, INET_ADDRSTRLEN - 1);

    /* EWMA başlangıç değerleri — en iyi durum ağ koşullarını varsay. */
    cc->avg_latency_ms   = 0.0;
    cc->packet_loss_pct  = 0.0;

    if (pthread_mutex_init(&cc->lock, NULL) != 0) {
        LOG_ERROR("cc_init: pthread_mutex_init basarisiz — %s", strerror(errno));
        return -1;
    }

    LOG_INFO("cc_init: denetleyici hazir (rol=%s, es=%s)",
             is_listener ? "DINLEYICI" : "ARAYICI", target_ip);
    return 0;
}

void cc_teardown(CommController *cc)
{
    if (!cc) return;

    cc_close_socket(cc);

    pthread_mutex_destroy(&cc->lock);

    /* Eski dosya tanımlayıcılarının yeniden kullanılmaması için tüm yapıyı sıfırla. */
    memset(cc, 0, sizeof(CommController));
    cc->active_fd = -1;

    LOG_INFO("cc_teardown: denetleyici kapatildi");
}

/* =========================================================================
 * Genel API — Soket Yönetimi
 * ========================================================================= */

int cc_create_tcp_socket(CommController *cc, uint16_t port)
{
    if (!cc) { errno = EINVAL; return -1; }

    /* Daha önce açık olan soketi kapat. */
    if (cc->active_fd >= 0)
        cc_close_socket(cc);

    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        LOG_ERROR("cc_create_tcp_socket: socket() basarisiz — %s", strerror(errno));
        cc->state = STATE_ERROR;
        return -1;
    }

    /* Geçişin ardından hızlı yeniden bağlamaya izin ver (TIME_WAIT sorununu önler). */
    int opt = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;

    if (cc->is_listener) {
        /* ---- Dinleyici: bind → listen → accept ---------------------------- */
        addr.sin_addr.s_addr = INADDR_ANY;
        addr.sin_port        = htons(port);

        if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            LOG_ERROR("cc_create_tcp_socket: bind(%u) basarisiz — %s",
                      port, strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
        if (listen(fd, TCP_BACKLOG) < 0) {
            LOG_ERROR("cc_create_tcp_socket: listen() basarisiz — %s", strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }

        LOG_TCP("%u numarali portta dinleniyor — arayici bekleniyor...", port);

        int client_fd = accept(fd, NULL, NULL);
        if (client_fd < 0) {
            LOG_ERROR("cc_create_tcp_socket: accept() basarisiz — %s", strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }

        /* Bundan sonra yalnızca kabul edilen soket gereklidir; sunucu fd'yi kapat. */
        close(fd);
        fd = client_fd;

    } else {
        /* ---- Arayıcı: connect --------------------------------------------- */
        if (inet_pton(AF_INET, cc->target_ip, &addr.sin_addr) <= 0) {
            LOG_ERROR("cc_create_tcp_socket: inet_pton('%s') basarisiz", cc->target_ip);
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
        addr.sin_port = htons(port);

        LOG_TCP("%s:%u adresine baglaniliyor...", cc->target_ip, port);

        if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            LOG_ERROR("cc_create_tcp_socket: connect(%s:%u) basarisiz — %s",
                      cc->target_ip, port, strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
    }

    cc->active_fd    = fd;
    cc->active_proto = PROTO_TCP;
    cc->target_port  = port;
    cc->state        = STATE_CONNECTED;

    LOG_TCP("%u numarali portta baglandi (fd=%d)", port, fd);
    return 0;
}

int cc_create_udp_socket(CommController *cc, uint16_t port)
{
    if (!cc) { errno = EINVAL; return -1; }

    if (cc->active_fd >= 0)
        cc_close_socket(cc);

    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        LOG_ERROR("cc_create_udp_socket: socket() basarisiz — %s", strerror(errno));
        cc->state = STATE_ERROR;
        return -1;
    }

    int opt = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;

    if (cc->is_listener) {
        /* ---- Dinleyici: veri birimlerinin gelmesi için porta bağlan ------- */
        addr.sin_addr.s_addr = INADDR_ANY;
        addr.sin_port        = htons(port);

        if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            LOG_ERROR("cc_create_udp_socket: bind(%u) basarisiz — %s",
                      port, strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
        LOG_UDP("Dinleyici olarak %u numarali porta baglandi (fd=%d)", port, fd);

    } else {
        /* ---- Arayıcı: connect() send()/recv() icin varsayilan uzak adresi ayarlar */
        if (inet_pton(AF_INET, cc->target_ip, &addr.sin_addr) <= 0) {
            LOG_ERROR("cc_create_udp_socket: inet_pton('%s') basarisiz", cc->target_ip);
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
        addr.sin_port = htons(port);

        if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            LOG_ERROR("cc_create_udp_socket: connect(%s:%u) basarisiz — %s",
                      cc->target_ip, port, strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
        LOG_UDP("Paket %s:%u adresine gonderildi (fd=%d)", cc->target_ip, port, fd);
    }

    cc->active_fd    = fd;
    cc->active_proto = PROTO_UDP;
    cc->target_port  = port;
    cc->state        = STATE_CONNECTED;
    return 0;
}

void cc_close_socket(CommController *cc)
{
    if (!cc || cc->active_fd < 0) return;

    /* Zarif TCP kapanışı (UDP için işlemsiz ancak zararsız). */
    shutdown(cc->active_fd, SHUT_RDWR);
    close(cc->active_fd);

    LOG_INFO("cc_close_socket: fd=%d kapatildi (%s, port %u)",
             cc->active_fd,
             cc->active_proto == PROTO_TCP ? "TCP" : "UDP",
             cc->target_port);

    cc->active_fd = -1;
    cc->state     = STATE_IDLE;
}

/* =========================================================================
 * Genel API — Protokol Değiştirme (Devirme / Handover)
 * SDD §5.1 Anahtar Yürütme sırası
 * ========================================================================= */

int cc_switch_protocol(CommController *cc, Protocol new_proto, uint16_t new_port)
{
    if (!cc) { errno = EINVAL; return -1; }

    pthread_mutex_lock(&cc->lock);
    cc->state = STATE_SWITCHING;
    pthread_mutex_unlock(&cc->lock);

    LOG_INFO("cc_switch_protocol: devirme baslatiliyor → %s port %u",
             new_proto == PROTO_TCP ? "TCP" : "UDP", new_port);

    /* ------------------------------------------------------------------
     * Adım 1 — Giden kuyruğu boşalt
     * Bekleyen verilerin gönderilmesi için HANDOVER_DRAIN_TIMEOUT_MS'ye kadar
     * döngüsel bekleme yapılır.
     * ------------------------------------------------------------------ */
    uint64_t deadline = now_ms() + HANDOVER_DRAIN_TIMEOUT_MS;
    while (now_ms() < deadline) {
        pthread_mutex_lock(&cc->lock);
        uint32_t pending = cc->buf_count;
        pthread_mutex_unlock(&cc->lock);
        if (pending == 0) break;
        sleep_ms(1);
    }

    if (cc->buf_count > 0) {
        LOG_WARN("cc_switch_protocol: kuyruk tam olarak boşaltılamadı (%u bayt kayboldu)",
                 cc->buf_count);
    }

    /* ------------------------------------------------------------------
     * Adım 2 — Eski soketi kapat
     * ------------------------------------------------------------------ */
    pthread_mutex_lock(&cc->lock);
    cc_close_socket(cc);
    pthread_mutex_unlock(&cc->lock);

    /* ------------------------------------------------------------------
     * Adım 3 — Yeni soketi aç
     * ------------------------------------------------------------------ */
    int rc;
    if (new_proto == PROTO_TCP)
        rc = cc_create_tcp_socket(cc, new_port);
    else
        rc = cc_create_udp_socket(cc, new_port);

    if (rc < 0) {
        LOG_ERROR("cc_switch_protocol: %u portunda yeni %s soketi açilamadi",
                  new_port, new_proto == PROTO_TCP ? "TCP" : "UDP");
        pthread_mutex_lock(&cc->lock);
        cc->state = STATE_ERROR;
        pthread_mutex_unlock(&cc->lock);
        return -1;
    }

    /* ------------------------------------------------------------------
     * Adım 4 — Devirme sırasında arabelleklenen mesajları gönder
     * ------------------------------------------------------------------ */
    pthread_mutex_lock(&cc->lock);
    if (flush_send_buf(cc) < 0) {
        LOG_WARN("cc_switch_protocol: flush_send_buf kismi hata");
    }
    cc->state = STATE_CONNECTED;
    pthread_mutex_unlock(&cc->lock);

    LOG_INFO("cc_switch_protocol: devirme tamamlandi → %s port %u",
             new_proto == PROTO_TCP ? "TCP" : "UDP", new_port);
    return 0;
}

/* =========================================================================
 * Genel API — Gönderme / Alma
 * ========================================================================= */

int cc_send(CommController *cc, MsgType type, uint32_t seq,
            const uint8_t *payload, uint32_t payload_len)
{
    if (!cc || cc->active_fd < 0) { errno = EBADF; return -1; }
    if (payload_len > MAX_PAYLOAD_LEN) { errno = EMSGSIZE; return -1; }
    if (!MSG_TYPE_VALID(type)) { errno = EINVAL; return -1; }

    /* Başlığı host bayt sırasında oluştur, ardından ağ bayt sırasına dönüştür. */
    pkt_header_t hdr;
    pkt_hdr_init(&hdr);
    hdr.msg_type    = (uint8_t)type;
    hdr.seq_num     = seq;
    hdr.payload_len = payload_len;
    pkt_hdr_to_network(&hdr);

    /* Önce başlığı yaz. */
    if (write_all(cc->active_fd, &hdr, PKT_HEADER_SIZE) < 0) {
        LOG_ERROR("cc_send: baslik yazma basarisiz — %s", strerror(errno));
        return -1;
    }

    /* Yükü yaz (varsa). */
    if (payload_len > 0 && payload != NULL) {
        if (write_all(cc->active_fd, payload, payload_len) < 0) {
            LOG_ERROR("cc_send: yuk yazma basarisiz — %s", strerror(errno));
            return -1;
        }
    }

    return (int)(PKT_HEADER_SIZE + payload_len);
}

int cc_recv(CommController *cc, pkt_header_t *hdr_out,
            uint8_t *payload_buf, uint32_t buf_size)
{
    if (!cc || cc->active_fd < 0 || !hdr_out) { errno = EBADF; return -1; }

    /* Başlık için tam olarak PKT_HEADER_SIZE bayt oku. */
    uint8_t raw_hdr[PKT_HEADER_SIZE];
    ssize_t n = read_all(cc->active_fd, raw_hdr, PKT_HEADER_SIZE);
    if (n <= 0) {
        if (n == 0)
            LOG_INFO("cc_recv: es baglantisini kesti");
        else
            LOG_ERROR("cc_recv: baslik okuma basarisiz — %s", strerror(errno));
        return (int)n;
    }

    /* Ham baytları yapıya kopyala (paketlenmiş, dolayısıyla güvenli). */
    memcpy(hdr_out, raw_hdr, PKT_HEADER_SIZE);
    pkt_hdr_from_network(hdr_out);

    /* Çözümlenen başlığın bütünlük denetimi. */
    if (hdr_out->version != PROTO_VERSION) {
        LOG_WARN("cc_recv: beklenmeyen protokol surumu %u", hdr_out->version);
    }
    if (!MSG_TYPE_VALID(hdr_out->msg_type)) {
        LOG_WARN("cc_recv: bilinmeyen msg_type %u", hdr_out->msg_type);
    }
    if (hdr_out->payload_len > MAX_PAYLOAD_LEN) {
        LOG_ERROR("cc_recv: payload_len %u MAX_PAYLOAD_LEN sınırını aştı", hdr_out->payload_len);
        errno = EMSGSIZE;
        return -1;
    }
    if (hdr_out->payload_len > buf_size) {
        LOG_ERROR("cc_recv: payload_len %u çağıran arabelleğini (%u) aştı",
                  hdr_out->payload_len, buf_size);
        errno = ENOBUFS;
        return -1;
    }

    /* Yük baytlarını oku. */
    if (hdr_out->payload_len > 0) {
        ssize_t pn = read_all(cc->active_fd, payload_buf, hdr_out->payload_len);
        if (pn <= 0) {
            LOG_ERROR("cc_recv: yuk okuma basarisiz — %s", strerror(errno));
            return -1;
        }
    }

    return (int)(PKT_HEADER_SIZE + hdr_out->payload_len);
}

/* =========================================================================
 * Genel API — Uyarlamalı Mantık (Otomatik Mod)
 * SDD §5.2 — Protokol Karar Mantığı Akışı
 * SRS  FR8  — Otomatik Taşıma
 * ========================================================================= */

int cc_update_network_health(CommController *cc,
                             double latency_ms, double loss_pct)
{
    if (!cc) return 0;

    pthread_mutex_lock(&cc->lock);

    /*
     * Üstel Ağırlıklı Hareketli Ortalama (EWMA):
     *   yeni_ort = α × örnek + (1 − α) × eski_ort
     *
     * α = HEALTH_EWMA_ALPHA (0.2) — geçici ani artışlara düşük duyarlılık.
     * İlk çağrıda (avg == 0.0) ilk örnekle doğrudan tohumlanır.
     */
    if (cc->avg_latency_ms == 0.0 && cc->packet_loss_pct == 0.0) {
        cc->avg_latency_ms  = latency_ms;
        cc->packet_loss_pct = loss_pct;
    } else {
        cc->avg_latency_ms  = HEALTH_EWMA_ALPHA * latency_ms
                            + (1.0 - HEALTH_EWMA_ALPHA) * cc->avg_latency_ms;
        cc->packet_loss_pct = HEALTH_EWMA_ALPHA * loss_pct
                            + (1.0 - HEALTH_EWMA_ALPHA) * cc->packet_loss_pct;
    }

    pthread_mutex_unlock(&cc->lock);
    return 0;
}

int cc_check_adaptive_switch(CommController *cc, uint16_t new_port)
{
    if (!cc) { errno = EINVAL; return -1; }

    pthread_mutex_lock(&cc->lock);
    double latency = cc->avg_latency_ms;
    double loss    = cc->packet_loss_pct;
    Protocol proto = cc->active_proto;
    pthread_mutex_unlock(&cc->lock);

    /*
     * SDD §5.2 karar tablosu:
     *   TIKANIC: gecikme > 200 ms VEYA kayıp > %5  → UDP tercih et
     *   KARARLI: gecikme ≤ 200 ms VE  kayıp ≤ %5  → TCP tercih et
     */
    int congested = (latency > LATENCY_THRESHOLD_MS || loss > LOSS_THRESHOLD_PCT);

    if (congested && proto == PROTO_TCP) {
        LOG_AUTO("Tikaclilik tespit edildi (gecikme=%.1fms, kayip=%.1f%%). "
                 "TCP → UDP gecisi port %u.", latency, loss, new_port);
        return cc_switch_protocol(cc, PROTO_UDP, new_port);
    }

    if (!congested && proto == PROTO_UDP) {
        LOG_AUTO("Ag kararli (gecikme=%.1fms, kayip=%.1f%%). "
                 "UDP → TCP'ye geri donuluyor, port %u.", latency, loss, new_port);
        return cc_switch_protocol(cc, PROTO_TCP, new_port);
    }

    /* Değişiklik gerekmiyor. */
    return 0;
}
