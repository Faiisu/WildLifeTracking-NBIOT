"""Run the actual LED work handler with host GPIO/work stubs; no hardware needed."""
from pathlib import Path
import subprocess
import tempfile

APP = Path(__file__).resolve().parents[1]
STUBS = {
    'zephyr/kernel.h': r'''
#pragma once
#include <stdbool.h>
#define IS_ENABLED(x) 0
#define ARG_UNUSED(x) (void)(x)
#define K_MSEC(x) (x)
#define K_NO_WAIT 0
struct k_work { int unused; };
struct k_work_delayable { struct k_work work; };
static int delay_ms, schedules;
static void k_work_init_delayable(struct k_work_delayable *w,
                                 void (*handler)(struct k_work *))
{ (void)w; (void)handler; }
static int k_work_reschedule(struct k_work_delayable *w, int delay)
{ (void)w; delay_ms = delay; schedules++; return 0; }
static int k_work_schedule(struct k_work_delayable *w, int delay)
{ return k_work_reschedule(w, delay); }
''',
    'zephyr/logging/log.h': r'''
#define LOG_MODULE_REGISTER(...)
#define LOG_ERR(...)
#define LOG_INF(...)
''',
    'zephyr/sys/atomic.h': r'''
#pragma once
#include <stdatomic.h>
typedef atomic_int atomic_t;
#define ATOMIC_INIT(x) (x)
static int atomic_get(atomic_t *a) { return atomic_load(a); }
static int atomic_set(atomic_t *a, int value) { return atomic_exchange(a, value); }
''',
    'zephyr/drivers/gpio.h': r'''
#define DT_ALIAS(x) 0
#define DT_NODE_HAS_STATUS(...) 1
#define GPIO_DT_SPEC_GET(...) {0}
#define GPIO_OUTPUT_INACTIVE 0
struct gpio_dt_spec { int unused; };
static int pin, gpio_error;
static bool gpio_is_ready_dt(const struct gpio_dt_spec *s)
{ (void)s; return true; }
static int gpio_pin_configure_dt(const struct gpio_dt_spec *s, int flags)
{ (void)s; pin = flags; return 0; }
static int gpio_pin_set_dt(const struct gpio_dt_spec *s, int value)
{ (void)s; pin = value; return gpio_error; }
''',
}
HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include "tracker_ui.c"
static void tick(void) { working_led_blink_handler(&working_led_work.work); }
static void check_group(enum tracker_ui_state state, int count)
{
    tracker_ui_set_state(state);
    tick();
    assert(pin == 0 && delay_ms == 1000);
    for (int repeat = 0; repeat < 2; repeat++) {
        for (int blink = 0; blink < count; blink++) {
            tick();
            assert(pin == 1 && delay_ms == 200);
            tick();
            assert(pin == 0);
            assert(delay_ms == (blink == count - 1 ? 1000 : 200));
        }
    }
    int before = schedules;
    tracker_ui_set_state(state);
    assert(schedules == before);
}
int main(void)
{
    assert(tracker_ui_init() == 0 && pin == 0);
    tick();
    assert(pin == 0);
    check_group(TRACKER_UI_GNSS, 2);
    check_group(TRACKER_UI_LTE, 3);
    check_group(TRACKER_UI_LTE_CONNECTED, 4);
    tick(); /* Interrupt a lit pulse with a new state. */
    assert(pin == 1);
    check_group(TRACKER_UI_GNSS, 2);
    tracker_ui_set_state(TRACKER_UI_IDLE);
    tick();
    assert(pin == 0 && delay_ms == 1000);
    tick();
    assert(pin == 0);
    gpio_error = -1;
    int before = schedules;
    tick();
    assert(schedules == before);
    puts("PASS: 2/3/4 blink groups, exact pause, repeats, state changes, idle, GPIO error");
}
'''

with tempfile.TemporaryDirectory(prefix='tracker-led-test-') as directory:
    root = Path(directory)
    for name, content in STUBS.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    (root / 'test.c').write_text(HARNESS)
    executable = root / 'test'
    subprocess.run([
        'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
        '-DCONFIG_STARTUP_LED=1', '-DCONFIG_WORKING_LED_BLINK_INTERVAL_MS=200',
        '-DCONFIG_WORKING_LED_GROUP_PAUSE_MS=1000',
        '-I', str(root), '-I', str(APP / 'src'), str(root / 'test.c'),
        '-o', str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)
