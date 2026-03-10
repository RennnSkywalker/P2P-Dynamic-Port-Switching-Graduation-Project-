/**
 * @file common.h
 * @brief Module 7 — Message/Packet Format Specification
 *
 * Defines the 10-byte custom protocol header used for all peer-to-peer
 * communications in the P2P Dynamic Port Switching system.
 *
 * Wire layout (big-endian / network byte order):
 *   Offset  Size  Field
 *   ------  ----  -----------
 *    0      1 B   version      (uint8_t)
 *    1      1 B   msg_type     (uint8_t)
 *    2      4 B   seq_num      (uint32_t, big-endian)
 *    6      4 B   payload_len  (uint32_t, big-endian)
 *   ------  ----
 *   Total: 10 bytes
 *
 * SDD Reference: Section 4.7 — Module 7 — Message/Packet Format Specification
 * SRS Reference: Section 3.5.1 — Performance: all computations < 5 ms
 *
 * Compatibility: C11, POSIX (Linux / macOS). Compiles on GCC and Clang.
 * Links with: comm_controller.c, port_mgmt.c (Module 3), timing_sync.c (Module 4)
 */

#ifndef COMMON_H
#define COMMON_H

/* =========================================================================
 * Standard includes — no platform-specific headers
 * ========================================================================= */
#include <stdint.h>   /* uint8_t, uint32_t                    */
#include <stddef.h>   /* size_t                               */
#include <arpa/inet.h>/* htonl(), ntohl() — POSIX / UNIX only */

/* =========================================================================
 * Protocol constants
 * ========================================================================= */

/** Current protocol version encoded in every packet header. */
#define PROTO_VERSION     ((uint8_t)1u)

/** Maximum accepted payload size (bytes). Prevents buffer overflows. */
#define MAX_PAYLOAD_LEN   4096u

/** Total size of the wire header in bytes — must always equal 10. */
#define PKT_HEADER_SIZE   10u

/* =========================================================================
 * Module 7 — Message Type Enum (SDD §4.7)
 * ========================================================================= */

/**
 * @brief Identifies the semantic purpose of a packet.
 *
 * Values are encoded as a single byte (uint8_t) in the header field
 * `msg_type`.  The integer assignments are fixed by the SDD and must not
 * be changed without a corresponding protocol-version bump.
 */
typedef enum {
    MSG_AUTH  = 1, /**< Authentication handshake packet            */
    MSG_DATA  = 2, /**< Application data / chat payload            */
    MSG_ACK   = 3, /**< Acknowledgement for UDP reliability layer  */
    MSG_ERROR = 4  /**< Error notification packet                  */
} MsgType;

/* =========================================================================
 * Module 7 — Packet Header Struct (SDD §4.7)
 *
 * __attribute__((packed)) guarantees exactly 10 bytes with no compiler-
 * inserted padding.  Fields are stored in HOST byte order in memory;
 * callers must use pkt_hdr_to_network() before sending and
 * pkt_hdr_from_network() after receiving.
 * ========================================================================= */

/**
 * @brief 10-byte protocol header.  All multi-byte fields are in host byte
 * order while in memory; convert before placing on the wire.
 */
typedef struct __attribute__((packed)) {
    uint8_t  version;     /**< Protocol version — set to PROTO_VERSION  */
    uint8_t  msg_type;    /**< One of MsgType enum values               */
    uint32_t seq_num;     /**< Sequence number for UDP ordering/ACK     */
    uint32_t payload_len; /**< Length of payload bytes following header */
} pkt_header_t;

/* Compile-time assertion: header must be exactly 10 bytes. */
_Static_assert(sizeof(pkt_header_t) == PKT_HEADER_SIZE,
               "pkt_header_t size must be exactly 10 bytes");

/* =========================================================================
 * Byte-order conversion helpers
 *
 * Call pkt_hdr_to_network() immediately before write()/send().
 * Call pkt_hdr_from_network() immediately after read()/recv().
 * Both functions operate in-place on a pointer to pkt_header_t.
 * ========================================================================= */

/**
 * @brief Convert header multi-byte fields from host to network (big-endian)
 *        byte order before placing on the wire.
 *
 * @param hdr  Pointer to the header to convert (modified in-place).
 */
static inline void pkt_hdr_to_network(pkt_header_t *hdr)
{
    hdr->seq_num     = htonl(hdr->seq_num);
    hdr->payload_len = htonl(hdr->payload_len);
}

/**
 * @brief Convert header multi-byte fields from network (big-endian) to host
 *        byte order after reading from the wire.
 *
 * @param hdr  Pointer to the header to convert (modified in-place).
 */
static inline void pkt_hdr_from_network(pkt_header_t *hdr)
{
    hdr->seq_num     = ntohl(hdr->seq_num);
    hdr->payload_len = ntohl(hdr->payload_len);
}

/**
 * @brief Initialise a header with sensible defaults.
 *
 * Sets version = PROTO_VERSION. Caller fills msg_type, seq_num,
 * and payload_len before sending.
 *
 * @param hdr  Header to initialise.
 */
static inline void pkt_hdr_init(pkt_header_t *hdr)
{
    hdr->version     = PROTO_VERSION;
    hdr->msg_type    = (uint8_t)MSG_DATA;
    hdr->seq_num     = 0u;
    hdr->payload_len = 0u;
}

/* =========================================================================
 * Utility macros
 * ========================================================================= */

/** Check whether a MsgType value is within the valid range. */
#define MSG_TYPE_VALID(t) ((t) >= MSG_AUTH && (t) <= MSG_ERROR)

#endif /* COMMON_H */
