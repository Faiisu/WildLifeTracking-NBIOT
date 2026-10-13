"""Exercise the actual state runner and button callback with host stubs."""
from pathlib import Path
import subprocess
import tempfile

APP = Path(__file__).resolve().parents[1]
SHARED = APP.parent / 'gps_mqtt_tracker' / 'src'
STUBS = {
    'zephyr/kernel.h': r'''
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define ARG_UNUSED(x) (void)(x)
#define K_SECONDS(x) (x)
#define K_FOREVER -1
typedef int k_timeout_t;
struct k_sem { int count; };
#define K_SEM_DEFINE(name, initial, limit) struct k_sem name = {initial}
static inline void k_sem_give(struct k_sem *s) { s->count = 1; }
static inline void k_sem_reset(struct k_sem *s) { s->count = 0; }
static inline int k_sem_take(struct k_sem *s, int timeout)
{ (void)timeout; if (!s->count) return -1; s->count = 0; return 0; }
''',
    'zephyr/device.h': r'''
#pragma once
#define DT_ALIAS(x) 0
#define DT_PARENT(x) 0
#define DT_NODE_HAS_STATUS(...) 1
#define DT_PROP(...) 11
#define DEVICE_DT_GET(...) NULL
static inline bool device_is_ready(const void *d) { (void)d; return true; }
''',
    'zephyr/input/input.h': r'''
#pragma once
#define INPUT_EV_KEY 1
struct input_event { int sync, type, code, value; };
#define INPUT_CALLBACK_DEFINE(...)
''',
    'zephyr/sys/atomic.h': r'''
#pragma once
#include <stdatomic.h>
typedef atomic_int atomic_t;
static inline int atomic_set(atomic_t *a, int v) { return atomic_exchange(a, v); }
static inline int atomic_get(atomic_t *a) { return atomic_load(a); }
static inline void atomic_clear(atomic_t *a) { atomic_store(a, 0); }
static inline bool atomic_cas(atomic_t *a, int old, int v)
{ return atomic_compare_exchange_strong(a, &old, v); }
''',
    'zephyr/logging/log.h': r'''
#pragma once
#include <stdio.h>
#define LOG_MODULE_REGISTER(...)
#define LOG_LEVEL_WRN 2
#define Z_LOG_LOCAL_DOMAIN_ID 0
#define LOG_DBG(...) LOG_INF(__VA_ARGS__)
#define LOG_INF(...) do { if (0) printf(__VA_ARGS__); } while (0)
#define LOG_ERR(...) LOG_INF(__VA_ARGS__)
#define LOG_WRN(...) LOG_INF(__VA_ARGS__)
''',
    'zephyr/logging/log_ctrl.h': r'''
#pragma once
static inline int log_source_id_get(const char *name) { (void)name; return 0; }
static inline int log_filter_set(const void *b, int d, int s, int level)
{ (void)b; (void)d; (void)s; return level; }
''',
    'modem/lte_lc.h': r'''
#pragma once
#define LTE_LC_EVT_NW_REG_STATUS 3
#define LTE_LC_EVT_PSM_UPDATE 4
#define LTE_LC_NW_REG_REGISTERED_HOME 1
#define LTE_LC_NW_REG_REGISTERED_ROAMING 5
#define LTE_LC_EVT_MODEM_SLEEP_ENTER 1
#define LTE_LC_EVT_MODEM_SLEEP_EXIT 2
#define LTE_LC_MODEM_SLEEP_PSM 1
struct lte_lc_evt { int type; struct { int type; } modem_sleep;
int nw_reg_status; struct { int tau, active_time; } psm_cfg; };
void lte_lc_register_handler(void (*handler)(const struct lte_lc_evt *const));
int lte_lc_psm_req(bool enable);
''',
    'nrf_modem_gnss.h': r'''
#pragma once
struct nrf_modem_gnss_datetime { int year, month, day, hour, minute, seconds; };
struct nrf_modem_gnss_pvt_data_frame {
    double latitude, longitude;
    float altitude, accuracy;
    struct nrf_modem_gnss_datetime datetime;
};
int nrf_modem_gnss_stop(void);
''',
}
HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <string.h>
#define main tracker_app_main
#include "main.c"
#undef main
static int gnss_error, gnss_wait_error, stop_error, radio_error, lte_error, mqtt_error;
static int publish_error, disconnect_error, power_error, psm_error;
static bool psm_requested;
static bool simulate_psm_sleep = true;
static int psm_requests;
static int starts, stops, uploads, connects, publishes, disconnects, powers;
static struct tracker_location_fix backlog[4];
static size_t count;
int tracker_ui_init(void) { return 0; }
void tracker_ui_set_state(enum tracker_ui_state s) { (void)s; }
void tracker_status_set(const char *s) { (void)s; }
int tracker_modem_init(void) { return 0; }
void lte_lc_register_handler(void (*handler)(const struct lte_lc_evt *const))
{ (void)handler; }
int lte_lc_psm_req(bool enable)
{ psm_requests++; if (!psm_error) psm_requested = enable; return psm_error; }
int tracker_mqtt_init(void) { return 0; }
int tracker_modem_return_to_gnss(void) { return radio_error; }
int tracker_gnss_start(void) { starts++; return gnss_error; }
int tracker_gnss_collect_until_stop(struct nrf_modem_gnss_pvt_data_frame *p,
                                  struct k_sem *stop)
{
    struct input_event press = {1, INPUT_EV_KEY, 11, 1};
    assert(waiting_for_button == 1);
    button_event(&press, NULL);
    assert(k_sem_take(stop, K_FOREVER) == 0 && waiting_for_button == 0);
    memset(p, 0, sizeof(*p)); p->latitude = 13.75; return gnss_wait_error;
}
int nrf_modem_gnss_stop(void) { stops++; return stop_error; }
void tracker_backlog_add(const struct tracker_location_fix *f) { backlog[count++] = *f; }
size_t tracker_backlog_count(void) { return count; }
bool tracker_backlog_peek_oldest(struct tracker_location_fix *f)
{ if (!count) return false; *f = backlog[0]; return true; }
void tracker_backlog_remove_oldest(void)
{ count--; memmove(backlog, backlog + 1, count * sizeof(*backlog)); }
int tracker_modem_prepare_upload(k_timeout_t t)
{ assert(t == 60); uploads++;
  if (psm_requested && !lte_error && simulate_psm_sleep) {
      struct lte_lc_evt e = {.type = LTE_LC_EVT_MODEM_SLEEP_ENTER,
      .modem_sleep.type = LTE_LC_MODEM_SLEEP_PSM};
      psm_event(&e);
  }
  return lte_error; }
int tracker_mqtt_connect(k_timeout_t t)
{ assert(t == 15); connects++; return mqtt_error; }
int tracker_mqtt_publish_location(const struct tracker_location_fix *f, k_timeout_t t)
{ assert(t == 15 && f->latitude == 13.75); publishes++; return publish_error; }
int tracker_mqtt_disconnect(k_timeout_t t)
{ assert(t == 10); disconnects++; return disconnect_error; }
int tracker_modem_enter_low_power(k_timeout_t t)
{ assert(t == 60); powers++; return power_error; }
int main(void)
{
    struct input_event press = {1, INPUT_EV_KEY, 11, 1};
    button_event(&press, NULL); /* Busy/init presses are discarded. */
    assert(step_requested.count == 0);
    atomic_set(&waiting_for_button, 1);
    struct input_event release = press;
    release.value = 0;
    button_event(&release, NULL);
    release = press; release.code = 12;
    button_event(&release, NULL);
    release = press; release.sync = 0;
    button_event(&release, NULL);
    assert(step_requested.count == 0);
    button_event(&press, NULL);
    assert(step_requested.count == 1 && waiting_for_button == 0);
    assert(k_sem_take(&step_requested, K_FOREVER) == 0);
    button_event(&press, NULL);
    assert(step_requested.count == 0);
    atomic_set(&waiting_for_button, 1);
    button_event(&press, NULL);
    assert(k_sem_take(&step_requested, K_FOREVER) == 0);

    assert(tracker_init() == 0 && starts == 0 && !psm_requested && psm_requests == 1);
    enum tracker_state s = run_state(TRACKER_STATE_WAIT_FOR_GNSS);
    assert(s == TRACKER_STATE_CONNECT_LTE && starts == 1 && stops == 1);
    assert(count == 1 && uploads == 0 && !gnss_needs_stop);
    assert(step_requested.count == 0 && waiting_for_button == 0);
    s = run_state(s);
    assert(s == TRACKER_STATE_CONNECT_MQTT && uploads == 1 && connects == 0);
    s = run_state(s);
    assert(s == TRACKER_STATE_PUBLISH_BACKLOG && connects == 1 && publishes == 0);
    s = run_state(s);
    assert(s == TRACKER_STATE_DISCONNECT_MQTT && count == 0 && disconnects == 0);
    s = run_state(s);
    assert(s == TRACKER_STATE_ENABLE_PSM && disconnects == 1 && powers == 0);
    s = run_state(s);
    assert(s == TRACKER_STATE_DISABLE_PSM && psm_requested && modem_in_psm && powers == 0);
    s = run_state(s);
    assert(s == TRACKER_STATE_WAIT_FOR_LOW_POWER && !psm_requested && !modem_in_psm);
    int starts_before_sleep = starts, uploads_before_sleep = uploads;
    s = run_state(s); /* A separate press is required to enter low power. */
    assert(s == TRACKER_STATE_LOW_POWER);
    assert(starts == starts_before_sleep && uploads == uploads_before_sleep);
    assert(run_state(s) == TRACKER_STATE_WAIT_FOR_GNSS);
    assert(starts == starts_before_sleep && uploads == uploads_before_sleep);

    gnss_error = -1;
    assert(run_state(TRACKER_STATE_WAIT_FOR_GNSS) == TRACKER_STATE_ENTER_LOW_POWER);
    assert(count == 0);
    gnss_error = 0;
    gnss_wait_error = -1;
    assert(run_state(TRACKER_STATE_WAIT_FOR_GNSS) == TRACKER_STATE_ENTER_LOW_POWER);
    assert(count == 0 && !gnss_needs_stop);
    gnss_wait_error = -ENODATA;
    assert(run_state(TRACKER_STATE_WAIT_FOR_GNSS) == TRACKER_STATE_CONNECT_LTE);
    assert(count == 1 && !backlog[0].has_fix && !gnss_needs_stop);
    tracker_backlog_remove_oldest();
    gnss_wait_error = -ETIMEDOUT;
    assert(run_state(TRACKER_STATE_WAIT_FOR_GNSS) == TRACKER_STATE_CONNECT_LTE);
    assert(count == 1 && !backlog[0].has_fix);
    tracker_backlog_remove_oldest();
    gnss_wait_error = 0;
    stop_error = -1;
    assert(run_state(TRACKER_STATE_WAIT_FOR_GNSS) == TRACKER_STATE_ENTER_LOW_POWER);
    assert(count == 1 && gnss_needs_stop);
    assert(run_state(TRACKER_STATE_ENTER_LOW_POWER) == TRACKER_STATE_ENTER_LOW_POWER);
    stop_error = 0;
    assert(run_state(TRACKER_STATE_ENTER_LOW_POWER) == TRACKER_STATE_DISABLE_PSM);
    radio_error = -1;
    int before = starts;
    assert(run_state(TRACKER_STATE_WAIT_FOR_GNSS) == TRACKER_STATE_ENTER_LOW_POWER);
    assert(starts == before);
    radio_error = 0;
    lte_error = -1;
    assert(run_state(TRACKER_STATE_CONNECT_LTE) == TRACKER_STATE_ENTER_LOW_POWER);
    assert(count == 1);
    mqtt_error = -1;
    assert(run_state(TRACKER_STATE_CONNECT_MQTT) == TRACKER_STATE_DISCONNECT_MQTT);
    publish_error = -1;
    assert(run_state(TRACKER_STATE_PUBLISH_BACKLOG) == TRACKER_STATE_DISCONNECT_MQTT);
    assert(count == 1);
    publish_error = 0;
    tracker_backlog_add(&backlog[0]);
    int sent_before = publishes;
    assert(run_state(TRACKER_STATE_PUBLISH_BACKLOG) == TRACKER_STATE_DISCONNECT_MQTT);
    assert(count == 0 && publishes == sent_before + 2);
    disconnect_error = -1;
    assert(run_state(TRACKER_STATE_DISCONNECT_MQTT) == TRACKER_STATE_DISCONNECT_MQTT);
    power_error = -1;
    assert(run_state(TRACKER_STATE_ENTER_LOW_POWER) == TRACKER_STATE_ENTER_LOW_POWER);
    power_error = 0;
    lte_error = 0;
    psm_error = -1;
    int upload_before = uploads, power_before = powers;
    assert(run_state(TRACKER_STATE_ENABLE_PSM) == TRACKER_STATE_DISABLE_PSM);
    assert(uploads == upload_before && powers == power_before);
    assert(run_state(TRACKER_STATE_DISABLE_PSM) == TRACKER_STATE_DISABLE_PSM);
    assert(tracker_init() == -1);
    psm_error = 0;
    radio_error = -1;
    int requests_before = psm_requests;
    assert(run_state(TRACKER_STATE_ENABLE_PSM) == TRACKER_STATE_DISABLE_PSM);
    assert(run_state(TRACKER_STATE_DISABLE_PSM) == TRACKER_STATE_DISABLE_PSM);
    assert(psm_requests == requests_before);
    radio_error = 0;
    lte_error = -1;
    assert(run_state(TRACKER_STATE_ENABLE_PSM) == TRACKER_STATE_DISABLE_PSM);
    assert(psm_requested && powers == power_before);
    assert(run_state(TRACKER_STATE_DISABLE_PSM) == TRACKER_STATE_WAIT_FOR_LOW_POWER);
    assert(!psm_requested);
    lte_error = 0;
    struct lte_lc_evt exit = {.type = LTE_LC_EVT_MODEM_SLEEP_EXIT,
        .modem_sleep.type = LTE_LC_MODEM_SLEEP_PSM};
    atomic_set(&modem_in_psm, 1);
    psm_event(&exit);
    assert(!modem_in_psm);
    simulate_psm_sleep = false;
    assert(run_state(TRACKER_STATE_ENABLE_PSM) == TRACKER_STATE_DISABLE_PSM);
    assert(!modem_in_psm);
    assert(run_state(TRACKER_STATE_DISABLE_PSM) == TRACKER_STATE_WAIT_FOR_LOW_POWER);
    puts("PASS: button gating, state transitions, backlog retention, PSM enable/disable, low-power wake, sleep events and failure cleanup");
}
'''
with tempfile.TemporaryDirectory(prefix='state-control-test-') as directory:
    root = Path(directory)
    for name, content in STUBS.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    (root / 'test.c').write_text(HARNESS)
    executable = root / 'test'
    subprocess.run([
        'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
        '-DCONFIG_TRACKER_PSM_ENTRY_TIMEOUT_SECONDS=60',
        '-DCONFIG_TRACKER_VERSION_MAJOR=1', '-DCONFIG_TRACKER_VERSION_MINOR=0',
        '-DCONFIG_TRACKER_VERSION_PATCH=0',
        '-I', str(root), '-I', str(APP / 'src'), '-I', str(SHARED),
        str(root / 'test.c'), '-o', str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)

# Verify the exact transmitted payload is displayed only after successful delivery.
STUBS.update({
    'zephyr/logging/log.h': r'''
#pragma once
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
static char log_output[8192];
static void capture_log(const char *fmt, ...)
{
    size_t used = strlen(log_output);
    va_list args;
    va_start(args, fmt);
    vsnprintf(log_output + used, sizeof(log_output) - used, fmt, args);
    va_end(args);
    strncat(log_output, "\n", sizeof(log_output) - strlen(log_output) - 1);
}
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...) capture_log(__VA_ARGS__)
#define LOG_WRN(...) LOG_INF(__VA_ARGS__)
#define LOG_ERR(...) LOG_INF(__VA_ARGS__)
#define LOG_DBG(...) do { if (0) printf(__VA_ARGS__); } while (0)
''',
    'nrf_modem_at.h': r'''
#pragma once
#include <stdio.h>
static inline int nrf_modem_at_cmd(char *b, size_t n, const char *cmd)
{ (void)cmd; snprintf(b, n, "123456789012345"); return 0; }
''',
    'modem/modem_key_mgmt.h': r'''
#pragma once
#define MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN 0
static inline int modem_key_mgmt_exists(int t, int c, bool *e)
{ (void)t; (void)c; *e = true; return 0; }
static inline int modem_key_mgmt_cmp(int t, int c, const void *b, size_t n)
{ (void)t; (void)c; (void)b; (void)n; return 0; }
static inline int modem_key_mgmt_delete(int t, int c) { (void)t; (void)c; return 0; }
static inline int modem_key_mgmt_write(int t, int c, const void *b, size_t n)
{ (void)t; (void)c; (void)b; (void)n; return 0; }
''',
    'net/mqtt_helper.h': r'''
#pragma once
#define MQTT_QOS_0_AT_MOST_ONCE 0
#define MQTT_QOS_1_AT_LEAST_ONCE 1
enum mqtt_qos { QOS0, QOS1 };
enum mqtt_conn_return_code { MQTT_CONNECTION_ACCEPTED, MQTT_UNACCEPTABLE_PROTOCOL_VERSION };
struct mqtt_helper_buf { const char *ptr; size_t size; };
struct mqtt_helper_conn_params {
    struct mqtt_helper_buf hostname, device_id, user_name, password;
};
struct mqtt_helper_cfg {
    struct {
        void (*on_connack)(enum mqtt_conn_return_code, bool);
        void (*on_disconnect)(int);
        void (*on_puback)(uint16_t, int);
    } cb;
};
struct mqtt_publish_param {
    struct {
        struct { uint8_t *data; size_t len; } payload;
        struct { enum mqtt_qos qos; struct { const char *utf8; size_t size; } topic; } topic;
    } message;
    uint16_t message_id;
};
static inline int mqtt_helper_init(const struct mqtt_helper_cfg *c) { (void)c; return 0; }
static inline int mqtt_helper_connect(const struct mqtt_helper_conn_params *c)
{ (void)c; return 0; }
static inline int mqtt_helper_disconnect(void) { return 0; }
static inline uint16_t mqtt_helper_msg_id_get(void) { return 1; }
int mqtt_helper_publish(const struct mqtt_publish_param *p);
''',
})
STUBS['zephyr/kernel.h'] += '\n#define IS_ENABLED(x) (x)\n'
MQTT_HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include "tracker_status.c"
#include "tracker_mqtt.c"
static char transmitted[256];
static bool acknowledge = true;
static int ack_result, send_error;
int mqtt_helper_publish(const struct mqtt_publish_param *p)
{
    assert(p->message.payload.len < sizeof(transmitted));
    memcpy(transmitted, p->message.payload.data, p->message.payload.len);
    transmitted[p->message.payload.len] = '\0';
    assert(strstr(log_output, "MQTT sending...") && !strstr(log_output, "MQTT sent"));
    if (acknowledge) on_mqtt_puback(p->message_id, ack_result);
    return send_error;
}
int main(void)
{
    struct tracker_location_fix fix = {
        .has_fix = true, .latitude = 13.75, .longitude = 100.5, .altitude = 12.0, .accuracy = 3.0,
        .datetime = {2026, 10, 13, 8, 9, 10},
    };
    tracker_status_set("GNSS FIX ACQUIRED");
    assert(strcmp(log_output, "STATUS: GNSS FIX ACQUIRED\n") == 0);
    tracker_status_set("MQTT PUBLISH");
    assert(strcmp(log_output, "STATUS: GNSS FIX ACQUIRED\n") == 0);
    log_output[0] = '\0';
    assert(tracker_mqtt_publish_location(&fix, 15) == 0);
    assert(strstr(log_output, "MQTT sent"));
    assert(strstr(log_output, transmitted));
    assert(strcmp(transmitted,
        "{\"lat\":13.750000,\"lon\":100.500000,\"alt\":12.0,\"acc\":3.0,"
        "\"time\":\"2026-10-13T08:09:10Z\"}") == 0);
    log_output[0] = '\0';
    send_error = -EIO;
    assert(tracker_mqtt_publish_location(&fix, 15) == -EIO);
    assert(!strstr(log_output, "MQTT sent"));
    send_error = 0;
    if (CONFIG_MQTT_PUB_QOS == 1) {
        log_output[0] = '\0';
        acknowledge = false;
        assert(tracker_mqtt_publish_location(&fix, 15) == -ETIMEDOUT);
        assert(!strstr(log_output, "MQTT sent") && !strstr(log_output, transmitted));
        log_output[0] = '\0';
        acknowledge = true; ack_result = -EIO;
        assert(tracker_mqtt_publish_location(&fix, 15) == -EIO);
        assert(!strstr(log_output, "MQTT sent"));
    }
    log_output[0] = '\0';
    acknowledge = true; ack_result = 0; send_error = 0;
    fix.has_fix = false;
    assert(tracker_mqtt_publish_location(&fix, 15) == 0);
    assert(strcmp(transmitted,
        "{\"lat\":null,\"lon\":null,\"alt\":null,\"acc\":null,\"time\":null}") == 0);
    assert(strstr(log_output, transmitted));
    puts("PASS: GNSS status, real/null JSON payloads, logging after success only");
}
'''
with tempfile.TemporaryDirectory(prefix='state-control-log-test-') as directory:
    root = Path(directory)
    for name, content in STUBS.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    (root / 'mqtt_test.c').write_text(MQTT_HARNESS)
    for qos in (0, 1):
        executable = root / f'mqtt_test_{qos}'
        subprocess.run([
            'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
            f'-DCONFIG_MQTT_PUB_QOS={qos}', '-DCONFIG_MQTT_LIB_TLS=0',
            '-DCONFIG_MQTT_HELPER_SEC_TAG=24', '-DCONFIG_MQTT_CLIENT_ID="test"',
            '-DCONFIG_MQTT_BROKER_HOSTNAME="example.invalid"',
            '-DCONFIG_MQTT_BROKER_PORT=8883', '-DCONFIG_MQTT_PUB_TOPIC="test"',
            '-DCONFIG_MQTT_BROKER_USERNAME=""', '-DCONFIG_MQTT_BROKER_PASSWORD=""',
            '-I', str(root), '-I', str(APP / 'src'), '-I', str(SHARED),
            str(root / 'mqtt_test.c'), '-o', str(executable),
        ], check=True)
        subprocess.run([str(executable)], check=True)
