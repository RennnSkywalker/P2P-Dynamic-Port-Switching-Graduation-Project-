/**
 * @file comm_controller.h
 * @brief Module 1 — Communication Controller (Public API)
 *
 * The Communication Controller is the central orchestrator for all Transport
 * Layer operations.  It manages:
 *   - TCP and UDP socket lifecycle (init, bind, connect, teardown)
 *   - Protocol state machine (IDLE → CONNECTED → SWITCHING → CONNECTED)
 *   - Handover logic: drains the outgoing queue, closes the old socket,
 *     re-establishes on the new port, then flushes buffered messages
 *   - Adaptive (Automatic) mode: switches TCP↔UDP based on network health
 *     thresholds defined in SDD §5.2 / SRS FR8
 *
 * SDD Reference: §4.1 (Module 1), §5.1 (Communication Controller Flow)
 * SRS Reference: FR8, §3.5.1 (Performance < 5 ms), §3.5.4 (Security)
 *
 * Designed to link with:
 *   - port_mgmt.c  (Module 3) — provides cc_extern_get_target_port()
 *   - timing_sync.c (Module 4) — provides cc_extern_get_time_step()
 *
 * Compatibility: C11, POSIX (Linux / macOS).
 */

#ifndef COMM_CONTROLLER_H
#define COMM_CONTROLLER_H

#include <stdint.h>
#include <netinet/in.h>  /* INET_ADDRSTRLEN                    */
#include <pthread.h>     /* pthread_mutex_t                    */
#include "common.h"      /* pkt_header_t, MsgType, PKT_HEADER_SIZE */

/* =========================================================================
 * Configuration constants
 * ========================================================================= */

/** Size of the in-memory outgoing message ring-buffer (bytes). */
#define SEND_BUF_SIZE        (64u * 1024u)   /* 64 KiB               */

/** Maximum time (ms) to drain the outgoing queue before a forced handover. */
#define HANDOVER_DRAIN_TIMEOUT_MS  200u

/** Latency threshold (ms) that triggers a switch to UDP (Automatic Mode). */
#define LATENCY_THRESHOLD_MS  200.0

/** Packet-loss percentage threshold that triggers a switch to UDP. */
#define LOSS_THRESHOLD_PCT      5.0

/** TCP listen backlog. */
#define TCP_BACKLOG              5

/** EWMA smoothing factor for network-health metrics (0 < α ≤ 1). */
#define HEALTH_EWMA_ALPHA        0.2

/* =========================================================================
 * Transport protocol enum
 * ========================================================================= */

/**
 * @brief Active transport protocol.
 */
typedef enum {
    PROTO_TCP = 0, /**< Reliable, connection-oriented (default)    */
    PROTO_UDP = 1  /**< Connectionless; used under high congestion  */
} Protocol;

/* =========================================================================
 * Controller state machine
 * ========================================================================= */

/**
 * @brief Lifecycle state of the communication controller.
 *
 * Transitions:
 *   IDLE ──(cc_create_tcp/udp_socket)──► CONNECTED
 *   CONNECTED ──(cc_switch_protocol)──► SWITCHING ──► CONNECTED | ERROR
 *   CONNECTED | ERROR ──(cc_teardown)──► IDLE
 */
typedef enum {
    STATE_IDLE       = 0, /**< Socket not yet opened              */
    STATE_CONNECTED  = 1, /**< Socket open and operational        */
    STATE_SWITCHING  = 2, /**< Handover in progress — queue paused */
    STATE_ERROR      = 3  /**< Unrecoverable socket error         */
} CommState;

/* =========================================================================
 * Central controller context
 * ========================================================================= */

/**
 * @brief All state for one peer's transport layer.
 *
 * Allocate on the stack or heap; initialise with cc_init() before use.
 * All fields whose names begin with an underscore are private; callers
 * should use the accessor functions only.
 */
typedef struct {
    /* ---- Socket -------------------------------------------------------- */
    int        active_fd;                   /**< Current socket fd (-1=none) */
    Protocol   active_proto;               /**< PROTO_TCP or PROTO_UDP       */
    CommState  state;                      /**< Controller state machine     */

    /* ---- Remote peer address ------------------------------------------ */
    char       target_ip[INET_ADDRSTRLEN]; /**< Peer IPv4 (dotted-decimal)  */
    uint16_t   target_port;               /**< Current target port          */
    int        is_listener;               /**< 1 = bind+accept, 0 = connect */

    /* ---- Outgoing ring-buffer ------------------------------------------ */
    uint8_t    send_buf[SEND_BUF_SIZE];   /**< Circular send buffer         */
    uint32_t   buf_head;                  /**< Write index                  */
    uint32_t   buf_tail;                  /**< Read index                   */
    uint32_t   buf_count;                 /**< Bytes currently buffered     */

    /* ---- Network health metrics (Automatic Mode) ----------------------- */
    double     avg_latency_ms;            /**< EWMA average RTT in ms       */
    double     packet_loss_pct;           /**< EWMA estimated loss (0–100)  */

    /* ---- Synchronisation ---------------------------------------------- */
    pthread_mutex_t lock; /**< Guards buf_* fields and state transitions    */
} CommController;

/* =========================================================================
 * External linkage stubs for future modules
 *
 * These symbols are declared here so comm_controller.c compiles in isolation.
 * They will be defined by port_mgmt.c (Module 3) and timing_sync.c (Module 4)
 * once those modules are integrated.
 * ========================================================================= */

/**
 * @brief [Module 3 stub] Return the dynamically computed target port.
 * @return Target port number, or 0 on error.
 */
extern uint16_t cc_extern_get_target_port(void);

/**
 * @brief [Module 4 stub] Return the current time-step index.
 * @return Monotonically increasing step counter.
 */
extern uint64_t cc_extern_get_time_step(void);

/* =========================================================================
 * Public API — Lifecycle
 * ========================================================================= */

/**
 * @brief Initialise a CommController context.
 *
 * Must be called once before any other cc_* function.  Zeroes all fields,
 * stores the target peer IP address, sets is_listener, and initialises the
 * POSIX mutex.
 *
 * @param cc         Pointer to an uninitialised CommController.
 * @param target_ip  IPv4 address of the remote peer (dotted-decimal string).
 * @param is_listener 1 if this peer should bind+listen; 0 if it should connect.
 * @return  0 on success, -1 on failure (errno is set).
 */
int cc_init(CommController *cc, const char *target_ip, int is_listener);

/**
 * @brief Release all resources held by a CommController.
 *
 * Closes the active socket (if open), destroys the mutex, and zeroes the
 * struct so it cannot be accidentally reused.
 *
 * @param cc  Initialised CommController to tear down.
 */
void cc_teardown(CommController *cc);

/* =========================================================================
 * Public API — Socket Management
 * ========================================================================= */

/**
 * @brief Create and establish a TCP socket.
 *
 * Listener path: socket → setsockopt(SO_REUSEADDR) → bind → listen → accept
 * Dialer path:   socket → setsockopt(SO_REUSEADDR) → connect
 *
 * On success, cc->active_fd is set and cc->state = STATE_CONNECTED.
 *
 * @param cc    Initialised controller.
 * @param port  Local port to bind (listener) or remote port to connect to (dialer).
 * @return 0 on success, -1 on error (errno set; detailed log written).
 */
int cc_create_tcp_socket(CommController *cc, uint16_t port);

/**
 * @brief Create and establish a UDP socket.
 *
 * Listener path: socket → bind(port)
 * Dialer path:   socket → connect(target_ip, port)  [sets default remote addr]
 *
 * On success, cc->active_fd is set and cc->state = STATE_CONNECTED.
 *
 * @param cc    Initialised controller.
 * @param port  Local port to bind (listener) or remote port for connect (dialer).
 * @return 0 on success, -1 on error.
 */
int cc_create_udp_socket(CommController *cc, uint16_t port);

/**
 * @brief Shut down and close the active socket.
 *
 * Calls shutdown(SHUT_RDWR) then close().  Safe to call when active_fd == -1.
 * Sets active_fd = -1 and state = STATE_IDLE.
 *
 * @param cc  Controller whose socket should be closed.
 */
void cc_close_socket(CommController *cc);

/* =========================================================================
 * Public API — Protocol Switching (Handover)
 * ========================================================================= */

/**
 * @brief Execute a full protocol handover to a new protocol and/or port.
 *
 * Implements the SDD §5.1 switch-execution sequence:
 *   1. Acquire mutex → set state = STATE_SWITCHING
 *   2. Drain outgoing queue (spin up to HANDOVER_DRAIN_TIMEOUT_MS)
 *   3. Close old socket
 *   4. Open new socket (TCP or UDP) on new_port
 *   5. Flush any buffered messages through the new socket
 *   6. Set state = STATE_CONNECTED (or STATE_ERROR on failure)
 *   7. Release mutex
 *
 * @param cc        Controller to switch.
 * @param new_proto Target protocol (PROTO_TCP or PROTO_UDP).
 * @param new_port  Target port for the new socket.
 * @return 0 on success, -1 on failure.
 */
int cc_switch_protocol(CommController *cc, Protocol new_proto, uint16_t new_port);

/* =========================================================================
 * Public API — Send / Receive
 * ========================================================================= */

/**
 * @brief Build and transmit a framed packet over the active socket.
 *
 * Constructs a pkt_header_t (sets version, msg_type, seq_num, payload_len),
 * converts to network byte order, and writes both header and payload.
 *
 * @param cc          Active controller.
 * @param type        Message type (MSG_AUTH, MSG_DATA, MSG_ACK, MSG_ERROR).
 * @param seq         Sequence number.
 * @param payload     Pointer to payload data (may be NULL if payload_len == 0).
 * @param payload_len Number of payload bytes.
 * @return Total bytes written (header + payload), or -1 on error.
 */
int cc_send(CommController *cc, MsgType type, uint32_t seq,
            const uint8_t *payload, uint32_t payload_len);

/**
 * @brief Receive one framed packet from the active socket.
 *
 * Reads exactly PKT_HEADER_SIZE bytes first, then reads payload_len bytes.
 * Validates payload_len against buf_size and against MAX_PAYLOAD_LEN.
 *
 * @param cc          Active controller.
 * @param hdr_out     Output: decoded header in host byte order.
 * @param payload_buf Output buffer for payload bytes.
 * @param buf_size    Capacity of payload_buf in bytes.
 * @return Total bytes received (header + payload), or -1 on error / disconnect.
 */
int cc_recv(CommController *cc, pkt_header_t *hdr_out,
            uint8_t *payload_buf, uint32_t buf_size);

/* =========================================================================
 * Public API — Adaptive Logic (Automatic Mode)
 * ========================================================================= */

/**
 * @brief Update smoothed network-health metrics using EWMA.
 *
 * The caller (or a monitoring thread) provides instantaneous measurements.
 * The controller applies exponential weighted moving average smoothing
 * (α = HEALTH_EWMA_ALPHA) to avoid thrashing on transient spikes.
 *
 * Thread-safe (acquires lock internally).
 *
 * @param cc         Controller to update.
 * @param latency_ms Instantaneous round-trip latency in milliseconds.
 * @param loss_pct   Instantaneous packet-loss estimate (0.0–100.0).
 * @return 0 always (reserved for future error reporting).
 */
int cc_update_network_health(CommController *cc,
                             double latency_ms, double loss_pct);

/**
 * @brief Evaluate network health and trigger an automatic protocol switch.
 *
 * Implements the Automatic Mode decision logic from SDD §5.2 / SRS FR8:
 *
 *   Congested (→ UDP): avg_latency_ms > 200 OR packet_loss_pct > 5
 *   Stable    (→ TCP): avg_latency_ms ≤ 200 AND packet_loss_pct ≤ 5
 *
 * If the required protocol already matches active_proto, no switch is made.
 *
 * @param cc       Controller to evaluate.
 * @param new_port Port to use if a switch is triggered.
 * @return  1 if a switch was triggered, 0 if no change needed, -1 on error.
 */
int cc_check_adaptive_switch(CommController *cc, uint16_t new_port);

#endif /* COMM_CONTROLLER_H */
