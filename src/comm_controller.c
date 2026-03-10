/**
 * @file comm_controller.c
 * @brief Module 1 — Communication Controller (Implementation)
 *
 * Full POSIX C11 implementation of the Communication Controller.
 * See comm_controller.h for the public API and design rationale.
 *
 * SDD Reference: §4.1, §5.1 (Communication Controller Flow)
 * SRS Reference: FR8, §3.5.1 (< 5 ms), §3.5.4, §3.5.5
 *
 * Build:
 *   gcc -std=c11 -Wall -Wextra -pedantic -pthread \
 *       comm_controller.c -o libcommctrl.o -c
 *
 * Notes:
 *   - All socket I/O uses blocking calls.  Non-blocking / async I/O may be
 *     added in a future version without changing the public API.
 *   - The outgoing ring-buffer is intentionally simple (byte-granular) to
 *     keep the handover logic auditable.
 *   - Module 3 (port_mgmt.c) and Module 4 (timing_sync.c) are referenced
 *     via weak-symbol stubs so this TU compiles standalone.
 */

/* =========================================================================
 * Feature-test macros — must precede all other includes
 * ========================================================================= */
#define _POSIX_C_SOURCE 200809L

/* =========================================================================
 * Includes
 * ========================================================================= */
#include "comm_controller.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <time.h>          /* clock_gettime, nanosleep            */
#include <unistd.h>        /* close(), read(), write()            */
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <pthread.h>

/* =========================================================================
 * Weak stubs for external module linkage (Module 3 & 4)
 *
 * These are compiled as __attribute__((weak)) so that when port_mgmt.c and
 * timing_sync.c are linked in, their strong symbols take precedence without
 * a linker error.
 * ========================================================================= */

__attribute__((weak)) uint16_t cc_extern_get_target_port(void)
{
    /* Placeholder — returns 0 until Module 3 is linked. */
    return 0;
}

__attribute__((weak)) uint64_t cc_extern_get_time_step(void)
{
    /* Placeholder — returns Unix seconds until Module 4 is linked. */
    return (uint64_t)time(NULL);
}

/* =========================================================================
 * Internal logging helpers
 * ========================================================================= */

/** Log prefix tokens matching the SDD §3.1.x console format. */
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
 * Internal helper — millisecond monotonic clock
 * ========================================================================= */

/**
 * @brief Return current monotonic time in milliseconds.
 * @return ms since some unspecified epoch (suitable for relative durations).
 */
static uint64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000ULL + (uint64_t)(ts.tv_nsec / 1000000L);
}

/**
 * @brief Sleep for a given number of milliseconds.
 * @param ms Milliseconds to sleep (approximate).
 */
static void sleep_ms(unsigned int ms)
{
    struct timespec req;
    req.tv_sec  = (time_t)(ms / 1000u);
    req.tv_nsec = (long)((ms % 1000u) * 1000000L);
    nanosleep(&req, NULL);
}

/* =========================================================================
 * Internal helper — reliable send / recv wrappers
 * ========================================================================= */

/**
 * @brief Write exactly `len` bytes to `fd`, retrying on EINTR.
 * @return `len` on success, -1 on error or peer disconnect.
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
 * @brief Read exactly `len` bytes from `fd`, retrying on EINTR.
 * @return `len` on success, 0 on clean disconnect, -1 on error.
 */
static ssize_t read_all(int fd, void *buf, size_t len)
{
    uint8_t *ptr     = (uint8_t *)buf;
    size_t remaining = len;

    while (remaining > 0) {
        ssize_t n = read(fd, ptr, remaining);
        if (n == 0) return 0;  /* EOF / peer disconnected */
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
 * Internal helper — ring-buffer flush
 *
 * Sends all bytes currently in cc->send_buf through the active socket.
 * Must be called with cc->lock held.
 * ========================================================================= */
static int flush_send_buf(CommController *cc)
{
    if (cc->buf_count == 0 || cc->active_fd < 0)
        return 0;

    /* The ring-buffer may wrap around; handle in up to two chunks. */
    uint32_t chunk1_start = cc->buf_tail;
    uint32_t chunk1_len;
    uint32_t chunk2_len;

    if (cc->buf_head > cc->buf_tail) {
        /* Contiguous */
        chunk1_len = cc->buf_count;
        chunk2_len = 0;
    } else {
        /* Wraps: from tail to end-of-buffer, then from 0 to head */
        chunk1_len = SEND_BUF_SIZE - cc->buf_tail;
        chunk2_len = cc->buf_head;
    }

    if (write_all(cc->active_fd, cc->send_buf + chunk1_start, chunk1_len) < 0) {
        LOG_WARN("flush_send_buf: write error on chunk1 — %s", strerror(errno));
        return -1;
    }
    if (chunk2_len > 0) {
        if (write_all(cc->active_fd, cc->send_buf, chunk2_len) < 0) {
            LOG_WARN("flush_send_buf: write error on chunk2 — %s", strerror(errno));
            return -1;
        }
    }

    cc->buf_head  = 0;
    cc->buf_tail  = 0;
    cc->buf_count = 0;
    return 0;
}

/* =========================================================================
 * Public API — Lifecycle
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

    /* Store target IP; strncpy guarantees NUL termination via memset above. */
    strncpy(cc->target_ip, target_ip, INET_ADDRSTRLEN - 1);

    /* EWMA starting values — assume best-case network. */
    cc->avg_latency_ms   = 0.0;
    cc->packet_loss_pct  = 0.0;

    if (pthread_mutex_init(&cc->lock, NULL) != 0) {
        LOG_ERROR("cc_init: pthread_mutex_init failed — %s", strerror(errno));
        return -1;
    }

    LOG_INFO("cc_init: controller ready (role=%s, peer=%s)",
             is_listener ? "LISTENER" : "DIALER", target_ip);
    return 0;
}

void cc_teardown(CommController *cc)
{
    if (!cc) return;

    cc_close_socket(cc);

    pthread_mutex_destroy(&cc->lock);

    /* Zero entire struct so stale file descriptors cannot be reused. */
    memset(cc, 0, sizeof(CommController));
    cc->active_fd = -1;

    LOG_INFO("cc_teardown: controller destroyed");
}

/* =========================================================================
 * Public API — Socket Management
 * ========================================================================= */

int cc_create_tcp_socket(CommController *cc, uint16_t port)
{
    if (!cc) { errno = EINVAL; return -1; }

    /* Close any previously open socket. */
    if (cc->active_fd >= 0)
        cc_close_socket(cc);

    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        LOG_ERROR("cc_create_tcp_socket: socket() failed — %s", strerror(errno));
        cc->state = STATE_ERROR;
        return -1;
    }

    /* Allow rapid rebind after a switch (avoids TIME_WAIT issues). */
    int opt = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;

    if (cc->is_listener) {
        /* ---- Listener: bind → listen → accept ----------------------------- */
        addr.sin_addr.s_addr = INADDR_ANY;
        addr.sin_port        = htons(port);

        if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            LOG_ERROR("cc_create_tcp_socket: bind(%u) failed — %s",
                      port, strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
        if (listen(fd, TCP_BACKLOG) < 0) {
            LOG_ERROR("cc_create_tcp_socket: listen() failed — %s", strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }

        LOG_TCP("Listening on port %u — waiting for dialer...", port);

        int client_fd = accept(fd, NULL, NULL);
        if (client_fd < 0) {
            LOG_ERROR("cc_create_tcp_socket: accept() failed — %s", strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }

        /* We only need the accepted socket from here on; close the server fd. */
        close(fd);
        fd = client_fd;

    } else {
        /* ---- Dialer: connect ---------------------------------------------- */
        if (inet_pton(AF_INET, cc->target_ip, &addr.sin_addr) <= 0) {
            LOG_ERROR("cc_create_tcp_socket: inet_pton('%s') failed", cc->target_ip);
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
        addr.sin_port = htons(port);

        LOG_TCP("Connecting to %s:%u ...", cc->target_ip, port);

        if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            LOG_ERROR("cc_create_tcp_socket: connect(%s:%u) failed — %s",
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

    LOG_TCP("Connected on port %u (fd=%d)", port, fd);
    return 0;
}

int cc_create_udp_socket(CommController *cc, uint16_t port)
{
    if (!cc) { errno = EINVAL; return -1; }

    if (cc->active_fd >= 0)
        cc_close_socket(cc);

    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        LOG_ERROR("cc_create_udp_socket: socket() failed — %s", strerror(errno));
        cc->state = STATE_ERROR;
        return -1;
    }

    int opt = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;

    if (cc->is_listener) {
        /* ---- Listener: bind to port so datagrams arrive here -------------- */
        addr.sin_addr.s_addr = INADDR_ANY;
        addr.sin_port        = htons(port);

        if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            LOG_ERROR("cc_create_udp_socket: bind(%u) failed — %s",
                      port, strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
        LOG_UDP("Bound (listener) on port %u (fd=%d)", port, fd);

    } else {
        /* ---- Dialer: connect() sets default remote addr for send()/recv() - */
        if (inet_pton(AF_INET, cc->target_ip, &addr.sin_addr) <= 0) {
            LOG_ERROR("cc_create_udp_socket: inet_pton('%s') failed", cc->target_ip);
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
        addr.sin_port = htons(port);

        if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            LOG_ERROR("cc_create_udp_socket: connect(%s:%u) failed — %s",
                      cc->target_ip, port, strerror(errno));
            close(fd);
            cc->state = STATE_ERROR;
            return -1;
        }
        LOG_UDP("Packet dispatched to %s:%u (fd=%d)", cc->target_ip, port, fd);
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

    /* Graceful TCP teardown (no-op for UDP but harmless). */
    shutdown(cc->active_fd, SHUT_RDWR);
    close(cc->active_fd);

    LOG_INFO("cc_close_socket: fd=%d closed (was %s on port %u)",
             cc->active_fd,
             cc->active_proto == PROTO_TCP ? "TCP" : "UDP",
             cc->target_port);

    cc->active_fd = -1;
    cc->state     = STATE_IDLE;
}

/* =========================================================================
 * Public API — Protocol Switching (Handover)
 * SDD §5.1 Switch Execution sequence
 * ========================================================================= */

int cc_switch_protocol(CommController *cc, Protocol new_proto, uint16_t new_port)
{
    if (!cc) { errno = EINVAL; return -1; }

    pthread_mutex_lock(&cc->lock);
    cc->state = STATE_SWITCHING;
    pthread_mutex_unlock(&cc->lock);

    LOG_INFO("cc_switch_protocol: initiating handover → %s port %u",
             new_proto == PROTO_TCP ? "TCP" : "UDP", new_port);

    /* ------------------------------------------------------------------
     * Step 1 — Drain the outgoing queue
     * Spin-wait up to HANDOVER_DRAIN_TIMEOUT_MS for pending data to be sent.
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
        LOG_WARN("cc_switch_protocol: queue not fully drained (%u bytes lost)",
                 cc->buf_count);
    }

    /* ------------------------------------------------------------------
     * Step 2 — Close the old socket
     * ------------------------------------------------------------------ */
    pthread_mutex_lock(&cc->lock);
    cc_close_socket(cc);
    pthread_mutex_unlock(&cc->lock);

    /* ------------------------------------------------------------------
     * Step 3 — Open the new socket
     * ------------------------------------------------------------------ */
    int rc;
    if (new_proto == PROTO_TCP)
        rc = cc_create_tcp_socket(cc, new_port);
    else
        rc = cc_create_udp_socket(cc, new_port);

    if (rc < 0) {
        LOG_ERROR("cc_switch_protocol: failed to open new %s socket on port %u",
                  new_proto == PROTO_TCP ? "TCP" : "UDP", new_port);
        pthread_mutex_lock(&cc->lock);
        cc->state = STATE_ERROR;
        pthread_mutex_unlock(&cc->lock);
        return -1;
    }

    /* ------------------------------------------------------------------
     * Step 4 — Flush any messages buffered during handover
     * ------------------------------------------------------------------ */
    pthread_mutex_lock(&cc->lock);
    if (flush_send_buf(cc) < 0) {
        LOG_WARN("cc_switch_protocol: flush_send_buf partial failure");
    }
    cc->state = STATE_CONNECTED;
    pthread_mutex_unlock(&cc->lock);

    LOG_INFO("cc_switch_protocol: handover complete → %s port %u",
             new_proto == PROTO_TCP ? "TCP" : "UDP", new_port);
    return 0;
}

/* =========================================================================
 * Public API — Send / Receive
 * ========================================================================= */

int cc_send(CommController *cc, MsgType type, uint32_t seq,
            const uint8_t *payload, uint32_t payload_len)
{
    if (!cc || cc->active_fd < 0) { errno = EBADF; return -1; }
    if (payload_len > MAX_PAYLOAD_LEN) { errno = EMSGSIZE; return -1; }
    if (!MSG_TYPE_VALID(type)) { errno = EINVAL; return -1; }

    /* Build header in host byte order, then convert to network byte order. */
    pkt_header_t hdr;
    pkt_hdr_init(&hdr);
    hdr.msg_type    = (uint8_t)type;
    hdr.seq_num     = seq;
    hdr.payload_len = payload_len;
    pkt_hdr_to_network(&hdr);

    /* Write header first. */
    if (write_all(cc->active_fd, &hdr, PKT_HEADER_SIZE) < 0) {
        LOG_ERROR("cc_send: header write failed — %s", strerror(errno));
        return -1;
    }

    /* Write payload (if any). */
    if (payload_len > 0 && payload != NULL) {
        if (write_all(cc->active_fd, payload, payload_len) < 0) {
            LOG_ERROR("cc_send: payload write failed — %s", strerror(errno));
            return -1;
        }
    }

    return (int)(PKT_HEADER_SIZE + payload_len);
}

int cc_recv(CommController *cc, pkt_header_t *hdr_out,
            uint8_t *payload_buf, uint32_t buf_size)
{
    if (!cc || cc->active_fd < 0 || !hdr_out) { errno = EBADF; return -1; }

    /* Read exactly PKT_HEADER_SIZE bytes for the header. */
    uint8_t raw_hdr[PKT_HEADER_SIZE];
    ssize_t n = read_all(cc->active_fd, raw_hdr, PKT_HEADER_SIZE);
    if (n <= 0) {
        if (n == 0)
            LOG_INFO("cc_recv: peer disconnected");
        else
            LOG_ERROR("cc_recv: header read failed — %s", strerror(errno));
        return (int)n;
    }

    /* Copy raw bytes into the struct (packed, so safe). */
    memcpy(hdr_out, raw_hdr, PKT_HEADER_SIZE);
    pkt_hdr_from_network(hdr_out);

    /* Sanity-check the decoded header. */
    if (hdr_out->version != PROTO_VERSION) {
        LOG_WARN("cc_recv: unexpected protocol version %u", hdr_out->version);
    }
    if (!MSG_TYPE_VALID(hdr_out->msg_type)) {
        LOG_WARN("cc_recv: unknown msg_type %u", hdr_out->msg_type);
    }
    if (hdr_out->payload_len > MAX_PAYLOAD_LEN) {
        LOG_ERROR("cc_recv: payload_len %u exceeds MAX_PAYLOAD_LEN", hdr_out->payload_len);
        errno = EMSGSIZE;
        return -1;
    }
    if (hdr_out->payload_len > buf_size) {
        LOG_ERROR("cc_recv: payload_len %u exceeds caller buffer %u",
                  hdr_out->payload_len, buf_size);
        errno = ENOBUFS;
        return -1;
    }

    /* Read payload bytes. */
    if (hdr_out->payload_len > 0) {
        ssize_t pn = read_all(cc->active_fd, payload_buf, hdr_out->payload_len);
        if (pn <= 0) {
            LOG_ERROR("cc_recv: payload read failed — %s", strerror(errno));
            return -1;
        }
    }

    return (int)(PKT_HEADER_SIZE + hdr_out->payload_len);
}

/* =========================================================================
 * Public API — Adaptive Logic (Automatic Mode)
 * SDD §5.2 — Protocol Decision Logic Flow
 * SRS  FR8  — Automatic Transport
 * ========================================================================= */

int cc_update_network_health(CommController *cc,
                             double latency_ms, double loss_pct)
{
    if (!cc) return 0;

    pthread_mutex_lock(&cc->lock);

    /*
     * Exponential Weighted Moving Average (EWMA):
     *   new_avg = α × sample + (1 − α) × old_avg
     *
     * Using α = HEALTH_EWMA_ALPHA (0.2) — low sensitivity to transient spikes.
     * On first call (avg == 0.0) we seed with the first sample directly.
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
     * SDD §5.2 decision table:
     *   CONGESTED: latency > 200 ms OR loss > 5 %  → prefer UDP
     *   STABLE:    latency ≤ 200 ms AND loss ≤ 5 %  → prefer TCP
     */
    int congested = (latency > LATENCY_THRESHOLD_MS || loss > LOSS_THRESHOLD_PCT);

    if (congested && proto == PROTO_TCP) {
        LOG_AUTO("Congestion detected (latency=%.1fms, loss=%.1f%%). "
                 "Switching TCP → UDP on port %u.", latency, loss, new_port);
        return cc_switch_protocol(cc, PROTO_UDP, new_port);
    }

    if (!congested && proto == PROTO_UDP) {
        LOG_AUTO("Network stable (latency=%.1fms, loss=%.1f%%). "
                 "Reverting UDP → TCP on port %u.", latency, loss, new_port);
        return cc_switch_protocol(cc, PROTO_TCP, new_port);
    }

    /* No change needed. */
    return 0;
}
