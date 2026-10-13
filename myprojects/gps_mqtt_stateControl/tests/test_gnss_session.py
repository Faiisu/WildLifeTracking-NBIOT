"""Exercise the real collector with deterministic PVT, button and time stubs."""
from pathlib import Path
import subprocess
import tempfile

APP = Path(__file__).resolve().parents[1]
collector = (APP / 'src/tracker_gnss.c').read_text().split(
    'int tracker_gnss_collect_until_stop(', 1)[1]
PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define CONFIG_TRACKER_PERIODIC_TIMEOUT 180
#define NRF_MODEM_GNSS_PVT_FLAG_FIX_VALID 1
#define K_FOREVER -1
#define K_SECONDS(x) ((x) * 1000)
#define K_MSEC(x) (x)
#define K_NO_WAIT 0
typedef int k_timepoint_t;
struct k_sem { int unused; };
struct nrf_modem_gnss_pvt_data_frame { int flags; double latitude; };
static int gnss_pvt_queue, now, reads, stop_after, purges, queue_error;
static struct nrf_modem_gnss_pvt_data_frame frames[4];
static int frame_count;
static const char *status;
static int sys_timepoint_calc(int timeout) { return now + timeout; }
static bool sys_timepoint_expired(int deadline) { return now >= deadline; }
static int k_sem_take(struct k_sem *s, int timeout)
{ (void)s; assert(timeout == 0); return stop_after >= 0 && reads >= stop_after ? 0 : -EAGAIN; }
static void k_msgq_purge(int *q) { (void)q; purges++; }
static int k_msgq_get(int *q, struct nrf_modem_gnss_pvt_data_frame *p, int timeout)
{
    (void)q; assert(timeout == 100); now += timeout;
    int index = reads++;
    if (queue_error) return queue_error;
    if (index >= frame_count) return -EAGAIN;
    *p = frames[index]; return 0;
}
static void tracker_status_set(const char *label) { status = label; }
#define LOG_INF(...) do { if (0) printf(__VA_ARGS__); } while (0)
#define LOG_WRN(...) LOG_INF(__VA_ARGS__)
#define LOG_ERR(...) LOG_INF(__VA_ARGS__)
'''
HARNESS = r'''
static void reset(void)
{
    now = reads = purges = frame_count = queue_error = 0;
    stop_after = -1;
    memset(frames, 0, sizeof(frames));
}
int main(void)
{
    struct k_sem stop = {0};
    struct nrf_modem_gnss_pvt_data_frame result = {0};
    reset(); stop_after = 0;
    assert(tracker_gnss_collect_until_stop(&result, &stop) == -ENODATA);
    assert(reads == 0 && purges == 1);
    assert(strcmp(status, "GNSS SESSION STOPPED") == 0);
    reset(); stop_after = 3; frame_count = 3;
    frames[0] = (struct nrf_modem_gnss_pvt_data_frame){1, 11};
    frames[1] = (struct nrf_modem_gnss_pvt_data_frame){1, 22};
    frames[2] = (struct nrf_modem_gnss_pvt_data_frame){0, 99};
    assert(tracker_gnss_collect_until_stop(&result, &stop) == 0);
    assert(result.latitude == 22 && reads == 3); /* Fix alone does not end session. */
    reset(); stop_after = 1;
    assert(tracker_gnss_collect_until_stop(&result, &stop) == -ENODATA);
    assert(now == 100); /* Button stays responsive with no PVT frames. */
    reset();
    assert(tracker_gnss_collect_until_stop(&result, &stop) == -ETIMEDOUT);
    assert(now == 180000);
    reset(); frame_count = 1;
    frames[0] = (struct nrf_modem_gnss_pvt_data_frame){1, 33};
    assert(tracker_gnss_collect_until_stop(&result, &stop) == 0);
    assert(result.latitude == 33 && now == 180000);
    reset(); queue_error = -EIO;
    assert(tracker_gnss_collect_until_stop(&result, &stop) == -EIO);
    assert(tracker_gnss_collect_until_stop(NULL, &stop) == -EINVAL);
    assert(tracker_gnss_collect_until_stop(&result, NULL) == -EINVAL);
    puts("PASS: manual stop with/without fix, latest valid fix, empty queue, 180s timeout and errors");
}
'''
with tempfile.TemporaryDirectory(prefix='gnss-session-test-') as directory:
    root = Path(directory)
    source = root / 'test.c'
    source.write_text(PRELUDE + '\nint tracker_gnss_collect_until_stop(' + collector + HARNESS)
    executable = root / 'test'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(source),
                    '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
