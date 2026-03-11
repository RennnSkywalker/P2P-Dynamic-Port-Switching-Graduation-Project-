/**
 * @file common.h
 * @brief Modül 7 — Mesaj/Paket Biçim Tanımlaması
 *
 * P2P Dinamik Port Değiştirme sistemindeki tüm eşler arası iletişimde
 * kullanılan 10 baytlık özel protokol başlığını tanımlar.
 *
 * Kablo üzeri düzen (big-endian / ağ bayt sırası):
 *   Ofset  Boyut  Alan
 *   -----  -----  -----------
 *    0      1 B   version      (uint8_t)
 *    1      1 B   msg_type     (uint8_t)
 *    2      4 B   seq_num      (uint32_t, big-endian)
 *    6      4 B   payload_len  (uint32_t, big-endian)
 *   -----  -----
 *   Toplam: 10 bayt
 *
 * SDD Referansı: Bölüm 4.7 — Modül 7 — Mesaj/Paket Biçim Tanımlaması
 * SRS Referansı: Bölüm 3.5.1 — Performans: tüm hesaplamalar < 5 ms
 *
 * Uyumluluk: C11, POSIX (Linux / macOS). GCC ve Clang ile derlenir.
 * Bağlantı: comm_controller.c, port_mgmt.c (Modül 3), timing_sync.c (Modül 4)
 */

#ifndef COMMON_H
#define COMMON_H

/* =========================================================================
 * Standart kütüphaneler — platforma özgü başlık dosyası kullanılmaz
 * ========================================================================= */
#include <stdint.h>   /* uint8_t, uint32_t                          */
#include <stddef.h>   /* size_t                                      */
#include <arpa/inet.h>/* htonl(), ntohl() — yalnızca POSIX / UNIX   */

/* =========================================================================
 * Protokol sabitleri
 * ========================================================================= */

/** Her paket başlığına yazılan güncel protokol sürümü. */
#define PROTO_VERSION     ((uint8_t)1u)

/** Kabul edilen azami yük boyutu (bayt). Arabellek taşmalarını engeller. */
#define MAX_PAYLOAD_LEN   4096u

/** Kablo üzerindeki başlığın bayt cinsinden toplam boyutu — her zaman 10 olmalıdır. */
#define PKT_HEADER_SIZE   10u

/* =========================================================================
 * Modül 7 — Mesaj Türü Numaralandırması (SDD §4.7)
 * ========================================================================= */

/**
 * @brief Paketin anlamsal amacını belirtir.
 *
 * Değerler, başlık alanı `msg_type` içinde tek bayt (uint8_t) olarak kodlanır.
 * Tamsayı atamaları SDD tarafından sabitlenmiştir; protokol sürümü
 * yükseltilmeden değiştirilemez.
 */
typedef enum {
    MSG_AUTH  = 1, /**< Kimlik doğrulama el sıkışma paketi              */
    MSG_DATA  = 2, /**< Uygulama verisi / sohbet yükü                   */
    MSG_ACK   = 3, /**< UDP güvenilirlik katmanı için onay paketi        */
    MSG_ERROR = 4  /**< Hata bildirim paketi                             */
} MsgType;

/* =========================================================================
 * Modül 7 — Paket Başlığı Yapısı (SDD §4.7)
 *
 * __attribute__((packed)) derleyicinin yapıya dolgu eklemesini engelleyerek
 * tam olarak 10 bayt garantisi sağlar. Alanlar bellekte HOST bayt sırasında
 * tutulur; göndermeden önce pkt_hdr_to_network(), almadan sonra
 * pkt_hdr_from_network() çağrılmalıdır.
 * ========================================================================= */

/**
 * @brief 10 baytlık protokol başlığı. Çok baytlı tüm alanlar
 * bellekte host bayt sırasındadır; kabloya yerleştirmeden önce dönüştürün.
 */
typedef struct __attribute__((packed)) {
    uint8_t  version;     /**< Protokol sürümü — PROTO_VERSION olarak ayarlanır  */
    uint8_t  msg_type;    /**< MsgType numaralandırma değerlerinden biri          */
    uint32_t seq_num;     /**< UDP sıralama/ACK için sıra numarası                */
    uint32_t payload_len; /**< Başlığı izleyen yük baytlarının uzunluğu           */
} pkt_header_t;

/* Derleme zamanı doğrulaması: başlık tam olarak 10 bayt olmalıdır. */
_Static_assert(sizeof(pkt_header_t) == PKT_HEADER_SIZE,
               "pkt_header_t boyutu tam olarak 10 bayt olmalidir");

/* =========================================================================
 * Bayt sırası dönüşüm yardımcıları
 *
 * Kabloya göndermeden hemen önce pkt_hdr_to_network() çağrılmalıdır.
 * read()/recv() sonrasında hemen pkt_hdr_from_network() çağrılmalıdır.
 * Her iki fonksiyon da pkt_header_t işaretçisi üzerinde yerinde çalışır.
 * ========================================================================= */

/**
 * @brief Kabloya koymadan önce başlığın çok baytlı alanlarını host'tan
 *        ağ (big-endian) bayt sırasına dönüştürür.
 *
 * @param hdr  Dönüştürülecek başlığın işaretçisi (yerinde değiştirilir).
 */
static inline void pkt_hdr_to_network(pkt_header_t *hdr)
{
    hdr->seq_num     = htonl(hdr->seq_num);
    hdr->payload_len = htonl(hdr->payload_len);
}

/**
 * @brief Kablodan okuduktan sonra başlığın çok baytlı alanlarını ağ
 *        (big-endian) bayt sırasından host bayt sırasına dönüştürür.
 *
 * @param hdr  Dönüştürülecek başlığın işaretçisi (yerinde değiştirilir).
 */
static inline void pkt_hdr_from_network(pkt_header_t *hdr)
{
    hdr->seq_num     = ntohl(hdr->seq_num);
    hdr->payload_len = ntohl(hdr->payload_len);
}

/**
 * @brief Başlığı makul varsayılan değerlerle başlatır.
 *
 * version = PROTO_VERSION olarak ayarlar. Çağıran, göndermeden önce
 * msg_type, seq_num ve payload_len alanlarını doldurur.
 *
 * @param hdr  Başlatılacak başlık.
 */
static inline void pkt_hdr_init(pkt_header_t *hdr)
{
    hdr->version     = PROTO_VERSION;
    hdr->msg_type    = (uint8_t)MSG_DATA;
    hdr->seq_num     = 0u;
    hdr->payload_len = 0u;
}

/* =========================================================================
 * Yardımcı makrolar
 * ========================================================================= */

/** Bir MsgType değerinin geçerli aralıkta olup olmadığını denetler. */
#define MSG_TYPE_VALID(t) ((t) >= MSG_AUTH && (t) <= MSG_ERROR)

#endif /* COMMON_H */
