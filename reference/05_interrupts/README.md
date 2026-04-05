# 05 — Interrupts Reference

How to respond to hardware events (GPIO pins, sensor ready signals, button presses)
without polling — and how to safely hand work off from an ISR to a thread.

---

## The golden rule of ISRs in Zephyr

> **Never do heavy work inside an ISR.**
> ISRs must be short and fast. Use `k_work_submit()` to defer processing to a thread.

```
Hardware pin changes
       │
       ▼
  ISR / gpio_callback()        ← runs at interrupt priority, VERY short
       │
  k_work_submit(&my_work)      ← hand off to work queue
       │
       ▼
  work_handler()               ← runs in thread context, safe to do anything
       │  (log, send UART, write flash, send over NB-IoT, etc.)
```

This exact pattern is already in your `src/main.c`:
- `gnss_event_handler()` — ISR callback from the modem
- `k_work_submit_to_queue(&gnss_work_q, &agnss_data_get_work)` — deferred to thread

---

## Samples in this folder

---

### `gpio_button/`
**Source:** `zephyr/samples/basic/button`

GPIO interrupt on a button pin — fires a callback on rising edge.
The SparkFun Thing Plus nRF9160 has `sw0` (button at GPIO0 pin 12) already defined in its DTS.

**How it works (`src/main.c`):**
```c
#include <zephyr/drivers/gpio.h>

// 1. Get button spec from devicetree alias "sw0"
static const struct gpio_dt_spec button = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
static struct gpio_callback button_cb_data;

// 2. ISR callback — runs at interrupt priority, KEEP IT SHORT
void button_pressed(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
    printk("Button pressed!\n");
    // For GPS project: submit work here instead of doing work directly
    // k_work_submit(&trigger_fix_work);
}

int main(void)
{
    // 3. Configure pin as input
    gpio_pin_configure_dt(&button, GPIO_INPUT);

    // 4. Configure interrupt — fires on rising edge (button release → active)
    gpio_pin_interrupt_configure_dt(&button, GPIO_INT_EDGE_TO_ACTIVE);

    // 5. Register callback
    gpio_init_callback(&button_cb_data, button_pressed, BIT(button.pin));
    gpio_add_callback(button.port, &button_cb_data);
}
```

**Interrupt trigger options:**
| Flag | When it fires |
|------|--------------|
| `GPIO_INT_EDGE_TO_ACTIVE` | Rising edge (inactive → active) |
| `GPIO_INT_EDGE_TO_INACTIVE` | Falling edge (active → inactive) |
| `GPIO_INT_EDGE_BOTH` | Both edges |
| `GPIO_INT_LEVEL_ACTIVE` | While pin is held active (level) |

**Apply to GPS project — trigger a GPS fix on button press:**
```c
static struct k_work trigger_fix_work;

static void trigger_fix_handler(struct k_work *work)
{
    // Safe to call from thread context
    LOG_INF("Button pressed — requesting GPS fix");
    location_request(&config);
}

void button_pressed(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
    k_work_submit(&trigger_fix_work);   // defer to thread
}

int main(void)
{
    k_work_init(&trigger_fix_work, trigger_fix_handler);
    // ... gpio setup ...
}
```

**Apply to GPS project — GPS data-ready pin from external sensor:**

Same pattern works for any external pin (e.g., a u-blox GPS module's PPS pulse or INT pin):
```c
// In your board overlay (.overlay file):
// &gpio0 {
//     gps-int-pin = <25 GPIO_ACTIVE_HIGH>;
// };

static const struct gpio_dt_spec gps_int = GPIO_DT_SPEC_GET(DT_NODELABEL(gps_int), gpios);

// Fires when external GPS module asserts its data-ready pin
void gps_data_ready_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
    k_work_submit(&read_gps_work);
}
```

---

### `isr_workqueue_dispatch/`
**Source:** `zephyr/samples/kernel/metairq_dispatch`

Advanced example: a **MetaIRQ thread** sits between the ISR and worker threads.
MetaIRQ threads run at a priority higher than all normal threads but lower than real ISRs —
perfect for time-sensitive dispatch without holding the interrupt line.

**Architecture:**
```
Hardware IRQ
    │
    ▼
metairq_thread  (K_HIGHEST_THREAD_PRIO)   ← receives msg, stamps latency, routes it
    │
    ├──► k_msgq_put → Thread 0  (priority -2)
    ├──► k_msgq_put → Thread 1  (priority -1)
    └──► k_msgq_put → Thread 2  (priority  0)
```

**Key concept from `src/main.c`:**
```c
// MetaIRQ thread defined at HIGHEST priority
K_THREAD_DEFINE(metairq_thread, STACK_SIZE, metairq_fn,
                NULL, NULL, NULL,
                K_HIGHEST_THREAD_PRIO,   // <-- higher than all normal threads
                K_USER, 0);

static void metairq_fn(void *p1, void *p2, void *p3)
{
    while (true) {
        struct msg m;
        message_dev_fetch(&m);                          // blocks until IRQ fires
        m.metairq_latency = k_cycle_get_32() - m.timestamp;
        k_msgq_put(&threads[m.target].msgq, &m, K_NO_WAIT);  // route to worker
    }
}
```

**Apply to GPS project — low-latency NMEA routing:**
```c
// Use metairq pattern if you need to process NMEA sentences from multiple sources
// and route them to different handler threads with minimal latency.
// For most projects, the simpler k_work pattern is sufficient.
```

> **Recommendation:** Use `gpio_button` pattern for your project.
> Use `isr_workqueue_dispatch` only if you need sub-millisecond ISR response times.

---

## Complete ISR → k_work pattern for GPS project

Copy this pattern into your `src/main.c` to add a hardware trigger:

```c
#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>

/* --- GPIO interrupt setup --- */
static const struct gpio_dt_spec trigger_btn = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
static struct gpio_callback trigger_cb_data;

/* --- Deferred work item --- */
static struct k_work on_trigger_work;

static void on_trigger_handler(struct k_work *work)
{
    /* This runs in thread context — safe to call any API */
    LOG_INF("Trigger received — starting GPS fix");
    /* nrf_modem_gnss_start() or location_request() here */
}

static void trigger_isr(const struct device *dev,
                         struct gpio_callback *cb, uint32_t pins)
{
    /* ISR context — ONLY submit work, nothing else */
    k_work_submit(&on_trigger_work);
}

int interrupt_init(void)
{
    k_work_init(&on_trigger_work, on_trigger_handler);

    gpio_pin_configure_dt(&trigger_btn, GPIO_INPUT);
    gpio_pin_interrupt_configure_dt(&trigger_btn, GPIO_INT_EDGE_TO_ACTIVE);
    gpio_init_callback(&trigger_cb_data, trigger_isr, BIT(trigger_btn.pin));
    gpio_add_callback(trigger_btn.port, &trigger_cb_data);

    return 0;
}
```

---

## Summary

| Use case | Pattern | Sample |
|----------|---------|--------|
| Button / sensor pin triggers GPS fix | GPIO callback + `k_work_submit` | `gpio_button/` |
| Multiple sensors routing data to threads | MetaIRQ dispatch | `isr_workqueue_dispatch/` |
| GNSS modem events (already in your project) | `gnss_event_handler` + `k_work` | `src/main.c` |
