# 03 — Threading & Concurrency Reference

Patterns for running GPS reading and NB-IoT transmission at the same time.

---

## Why threading matters in your GPS project

Your `main.c` already uses these Zephyr primitives — study them to extend it:

| Primitive | Already used in your project | Learn from |
|-----------|------------------------------|------------|
| `k_msgq` | NMEA string queue (`nmea_queue`) | `philosophers/` |
| `k_sem` | `pvt_data_sem`, `time_sem` | `synchronization/` |
| `k_work` | `agnss_data_get_work` | `philosophers/` |
| `k_poll` | waiting for PVT + NMEA events | `synchronization/` |

---

## Samples in this folder

---

### `synchronization/`
**Source:** `zephyr/samples/synchronization`

Two threads taking turns using a semaphore — the simplest threading example.

**Key concepts:**
- `k_sem_give()` / `k_sem_take()` for signaling between threads
- Thread priority and `k_sleep()`
- How the Zephyr scheduler works

**Pattern (from `src/main.c`):**
```c
K_SEM_DEFINE(my_sem, 0, 1);    // define semaphore (init=0, max=1)

// Thread A — producer (e.g., GNSS event handler)
void gnss_thread(void) {
    while (1) {
        // ... got a GPS fix ...
        k_sem_give(&my_sem);   // signal thread B
    }
}

// Thread B — consumer (e.g., send over NB-IoT)
void send_thread(void) {
    while (1) {
        k_sem_take(&my_sem, K_FOREVER);  // wait for GPS fix
        // ... send GPS data via UDP/MQTT ...
    }
}

// Register threads
K_THREAD_DEFINE(gnss_tid,  1024, gnss_thread,  NULL, NULL, NULL, 5, 0, 0);
K_THREAD_DEFINE(send_tid,  1024, send_thread,  NULL, NULL, NULL, 7, 0, 0);
```

**Apply to your project:**
Use this pattern to separate GPS reading (high priority) from network transmission (lower priority).

---

### `philosophers/`
**Source:** `zephyr/samples/philosophers`

Classic dining philosophers — 5 threads competing for shared resources (forks = mutexes).

**Key concepts:**
- `k_mutex_lock()` / `k_mutex_unlock()` — protect shared data
- Multiple threads at different priorities
- Deadlock avoidance patterns
- `CONFIG_NUM_PHILOSOPHERS` to scale thread count

**Pattern:**
```c
K_MUTEX_DEFINE(gps_data_mutex);

// Safe way to read/write shared GPS coordinates:
struct gps_fix {
    double lat;
    double lon;
    int64_t timestamp;
};

static struct gps_fix latest_fix;

// GNSS thread — writes fix
void gnss_thread(void) {
    k_mutex_lock(&gps_data_mutex, K_FOREVER);
    latest_fix.lat = pvt.latitude;
    latest_fix.lon = pvt.longitude;
    latest_fix.timestamp = k_uptime_get();
    k_mutex_unlock(&gps_data_mutex);
}

// Send thread — reads fix
void send_thread(void) {
    k_mutex_lock(&gps_data_mutex, K_FOREVER);
    double lat = latest_fix.lat;   // safe copy
    double lon = latest_fix.lon;
    k_mutex_unlock(&gps_data_mutex);
    // ... transmit lat/lon ...
}
```

---

## Recommended architecture for your GPS + NB-IoT project

```
┌─────────────────────────────────────────────────────────┐
│                     nRF9160 Threads                     │
│                                                         │
│  [GNSS event handler]  ──k_msgq──>  [Main thread]      │
│       (IRQ context)                  k_poll() loop      │
│                                           │             │
│                                      k_sem_give()       │
│                                           │             │
│                                    [Send thread]        │
│                                    UDP / MQTT / CoAP    │
└─────────────────────────────────────────────────────────┘
```

**Priority order (lower number = higher priority):**
```
GNSS event handler   → priority 3  (time-sensitive, don't miss events)
Main processing      → priority 5
Network send thread  → priority 7  (can wait, NB-IoT is slow)
```

---

## Key Zephyr threading APIs — quick reference

```c
// Semaphore
K_SEM_DEFINE(name, initial_count, limit);
k_sem_give(&name);
k_sem_take(&name, K_FOREVER);   // or K_MSEC(500) for timeout

// Mutex
K_MUTEX_DEFINE(name);
k_mutex_lock(&name, K_FOREVER);
k_mutex_unlock(&name);

// Message queue (typed FIFO)
K_MSGQ_DEFINE(name, sizeof(item), max_items, alignment);
k_msgq_put(&name, &item, K_NO_WAIT);
k_msgq_get(&name, &item, K_FOREVER);

// Work queue (deferred execution, not blocking ISR)
static struct k_work my_work;
k_work_init(&my_work, my_work_handler);
k_work_submit(&my_work);

// Thread creation
K_THREAD_DEFINE(tid, stack_size, entry_fn, p1, p2, p3, priority, options, delay);
```
