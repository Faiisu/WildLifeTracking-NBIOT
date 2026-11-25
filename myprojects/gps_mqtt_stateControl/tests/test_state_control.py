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
static bool auto_press;
#define K_SEM_DEFINE(name, initial, limit) struct k_sem name = {initial}
static inline void k_sem_give(struct k_sem *s) { s->count = 1; }
static inline void k_sem_reset(struct k_sem *s) { s->count = 0; }
static inline int k_sem_take(struct k_sem *s, int timeout)
{ if (timeout == K_FOREVER && auto_press) { s->count = 1; auto_press = false; }
  if (!s->count) return -1; s->count = 0; return 0; }
static inline int64_t k_uptime_get(void) { return 48210; }
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
static int gnss_error, collect_error, stop_error, radio_error, lte_error, psm_error;
static bool psm_requested, simulate_sleep = true, finish_with_button = true;
static int starts, stops, connections, psm_requests;
static int mqtt_connections, mqtt_sends, mqtt_closes;
static int mqtt_connect_error, mqtt_send_error, mqtt_close_error;
int tracker_mqtt_init(void) { return 0; }
int tracker_mqtt_connect(k_timeout_t t)
{ assert(t == 15); mqtt_connections++; return mqtt_connect_error; }
int tracker_mqtt_disconnect(k_timeout_t t)
{ assert(t == 10); mqtt_closes++; return mqtt_close_error; }
int tracker_mqtt_publish_location(const struct tracker_location_fix *fix, k_timeout_t t)
{ assert(t == 15 && fix == &report); mqtt_sends++; return mqtt_send_error; }
static enum tracker_ui_state shown_ui;
int tracker_ui_init(void) { return 0; }
void tracker_ui_set_state(enum tracker_ui_state s) { shown_ui = s; }
int tracker_modem_init(void) { return 0; }
void lte_lc_register_handler(void (*handler)(const struct lte_lc_evt *const))
{ (void)handler; }
int lte_lc_psm_req(bool enable)
{ psm_requests++; if (!psm_error) psm_requested = enable; return psm_error; }
int tracker_modem_return_to_gnss(void) { return radio_error; }
int tracker_gnss_start(void) { starts++; return gnss_error; }
int tracker_gnss_collect_until_stop(struct nrf_modem_gnss_pvt_data_frame *p,
                                  struct k_sem *stop, bool *by_button)
{
    struct input_event press = {1, INPUT_EV_KEY, 11, 1};
    assert(waiting_for_button == 1);
    if (finish_with_button) {
        button_event(&press, NULL);
        assert(k_sem_take(stop, K_FOREVER) == 0 && waiting_for_button == 0);
    } else {
        auto_press = true; /* MODE after the timeout still gates FIX. */
    }
    *by_button = finish_with_button;
    memset(p, 0, sizeof(*p)); p->latitude = 13.75; return collect_error;
}
int nrf_modem_gnss_stop(void) { stops++; return stop_error; }
int tracker_modem_prepare_upload(k_timeout_t t)
{
    assert(t == 60); connections++;
    if (psm_requested && !lte_error && simulate_sleep) {
        struct lte_lc_evt e = {.type = LTE_LC_EVT_MODEM_SLEEP_ENTER,
            .modem_sleep.type = LTE_LC_MODEM_SLEEP_PSM};
        modem_event(&e);
    }
    return lte_error;
}
int main(void)
{
    struct input_event press = {1, INPUT_EV_KEY, 11, 1};
    button_event(&press, NULL);
    assert(step_requested.count == 0);
    atomic_set(&waiting_for_button, 1);
    struct input_event release = press; release.value = 0;
    button_event(&release, NULL);
    assert(step_requested.count == 0);
    button_event(&press, NULL);
    assert(step_requested.count == 1 && !waiting_for_button);
    assert(k_sem_take(&step_requested, K_FOREVER) == 0);
    button_event(&press, NULL);
    assert(step_requested.count == 0);

    assert(enter_state(TRACKER_STATE_GPS) == 0 && has_fix);
    assert(starts == 1 && stops == 1 && !gnss_needs_stop && !psm_requested);
    assert(!waiting_for_button && !step_requested.count && connections == 0);
    assert(next_state(TRACKER_STATE_GPS) == TRACKER_STATE_FIX);
    assert(enter_state(TRACKER_STATE_FIX) == 0 && shown_ui == TRACKER_UI_IDLE);
    assert(connections == 0);
    assert(next_state(TRACKER_STATE_FIX) == TRACKER_STATE_LTE_NO_PSM);
    assert(enter_state(TRACKER_STATE_LTE_NO_PSM) == 0 && !psm_requested);
    assert(connections == 1);
    assert(next_state(TRACKER_STATE_LTE_NO_PSM) == TRACKER_STATE_LTE_PSM);
    assert(enter_state(TRACKER_STATE_LTE_PSM) == 0 && psm_requested && modem_in_psm);
    assert(next_state(TRACKER_STATE_LTE_PSM) == TRACKER_STATE_MQTT_CONNECT);
    assert(enter_state(TRACKER_STATE_MQTT_CONNECT) == 0 && mqtt_connections == 1);
    assert(psm_requested && mqtt_sends == 0);
    assert(next_state(TRACKER_STATE_MQTT_CONNECT) == TRACKER_STATE_MQTT_SENDING);
    assert(enter_state(TRACKER_STATE_MQTT_SENDING) == 0 && mqtt_sends == 1);
    assert(report.has_fix && report.latitude == 13.75);
    assert(next_state(TRACKER_STATE_MQTT_SENDING) == TRACKER_STATE_MQTT_DISCONNECT);
    assert(enter_state(TRACKER_STATE_MQTT_DISCONNECT) == 0);
    assert(next_state(TRACKER_STATE_MQTT_DISCONNECT) == TRACKER_STATE_LOW_POWER);
    assert(enter_state(TRACKER_STATE_LOW_POWER) == 0 && !psm_requested);
    assert(shown_ui == TRACKER_UI_LOW_POWER && !modem_in_psm);
    assert(next_state(TRACKER_STATE_LOW_POWER) == TRACKER_STATE_GPS);

    collect_error = -ENODATA;
    assert(enter_state(TRACKER_STATE_GPS) == 0 && !has_fix);
    assert(enter_state(TRACKER_STATE_FIX) == 0 && !report.has_fix);
    assert(enter_state(TRACKER_STATE_MQTT_SENDING) == 0 && !report.has_fix);
    finish_with_button = false; collect_error = -ETIMEDOUT;
    assert(enter_state(TRACKER_STATE_GPS) == 0 && !has_fix && !auto_press);
    collect_error = 0;
    assert(enter_state(TRACKER_STATE_GPS) == 0 && has_fix && !auto_press);
    finish_with_button = true;
    gnss_error = -EIO;
    assert(enter_state(TRACKER_STATE_GPS) == -EIO && !gnss_needs_stop);
    gnss_error = 0; stop_error = -EIO;
    assert(enter_state(TRACKER_STATE_GPS) == -EIO && gnss_needs_stop);
    assert(enter_state(TRACKER_STATE_LOW_POWER) == -EIO);
    stop_error = 0;
    assert(enter_state(TRACKER_STATE_LOW_POWER) == 0 && !gnss_needs_stop);
    psm_error = -EIO;
    int before = connections;
    assert(enter_state(TRACKER_STATE_LTE_NO_PSM) == -EIO);
    assert(enter_state(TRACKER_STATE_LTE_PSM) == -EIO && connections == before);
    psm_error = 0; radio_error = -EIO;
    assert(enter_state(TRACKER_STATE_LOW_POWER) == -EIO);
    radio_error = 0; lte_error = -ETIMEDOUT;
    assert(enter_state(TRACKER_STATE_LTE_NO_PSM) == -ETIMEDOUT);
    lte_error = 0; simulate_sleep = false;
    assert(enter_state(TRACKER_STATE_LTE_PSM) == 0 && psm_requested && !modem_in_psm);
    assert(enter_state(TRACKER_STATE_LOW_POWER) == 0);
    mqtt_connect_error = -ETIMEDOUT;
    assert(enter_state(TRACKER_STATE_MQTT_CONNECT) == -ETIMEDOUT);
    mqtt_connect_error = 0;
    mqtt_send_error = -EIO;
    assert(enter_state(TRACKER_STATE_MQTT_SENDING) == -EIO && mqtt_send_retry);
    int reconnect_before = mqtt_connections;
    mqtt_send_error = 0;
    assert(enter_state(TRACKER_STATE_MQTT_SENDING) == 0 && !mqtt_send_retry);
    assert(mqtt_connections == reconnect_before + 1);
    mqtt_close_error = -EIO;
    assert(enter_state(TRACKER_STATE_MQTT_DISCONNECT) == -EIO);
    assert(enter_state(TRACKER_STATE_MQTT_CONNECT) == -EIO);
    mqtt_close_error = 0;
    puts("PASS: eight-state MQTT cycle, manual GPS stop, timeout gating, no-fix, PSM refusal, cleanup and retry");
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
        .has_fix = true, .uptime_ms = 48210, .latitude = 13.75, .longitude = 100.5,
        .altitude = 12.0, .accuracy = 3.0,
        .datetime = {2026, 10, 13, 8, 9, 10},
    };
    assert(tracker_mqtt_init() == 0);
    log_output[0] = '\0';
    tracker_status_set("GNSS FIX ACQUIRED");
    assert(strcmp(log_output, "STATUS: GNSS FIX ACQUIRED\n") == 0);
    tracker_status_set("MQTT PUBLISH");
    assert(strcmp(log_output, "STATUS: GNSS FIX ACQUIRED\n") == 0);
    log_output[0] = '\0';
    assert(tracker_mqtt_publish_location(&fix, 15) == 0);
    assert(strstr(log_output, "MQTT sent"));
    assert(strstr(log_output, transmitted));
    assert(strcmp(transmitted,
        "{\"device_id\":\"test\",\"uptime_ms\":48210,\"source\":\"gnss\","
        "\"gnss\":{\"lat\":13.750000,\"lon\":100.500000,\"alt\":12.0,\"acc\":3.0,"
        "\"time\":\"2026-10-13T08:09:10Z\"},\"lte\":null}") == 0);
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
        "{\"device_id\":\"test\",\"uptime_ms\":48210,\"source\":\"gnss\","
        "\"gnss\":null,\"lte\":null}") == 0);
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
