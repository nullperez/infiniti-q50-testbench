/*
 * esp32-bench firmware (protocol v1) for the Q50 combination-meter bench.
 *
 * Line protocol on UART0 (USB-UART bridge), 115200 8N1, '\n' terminated:
 *   PING                 -> OK PONG bench 1
 *   LIST                 -> OK LIST NAME:kind:pin ...      (pin -1 = not wired)
 *   STATUS               -> OK STATUS NAME=0|1|- ...       ('-' = not wired)
 *   SET <NAME> <0|1>     -> OK SET NAME v                  (power/input channels)
 *   PRESS <NAME> <ms>    -> OK PRESS NAME ms, auto-release after ms (button channels)
 *   ALLOFF               -> OK ALLOFF
 * Errors: ERR <CODE> [detail]  (UNKNOWN, BADARG, NOTWIRED, KIND, INTERLOCK <other>)
 * Events: EVT READY bench 1 (boot, everything off), EVT STATE NAME v (every output change)
 *
 * Compatible with q50tb/hw/esp32_bench.py and the original esp32-bench client.
 * Adds the ACC power channel.
 *
 * The pin table below is the owner's bench wiring, copied from the old
 * firmware's LIST reply (2026-10-12). Check it against LIST before flashing a
 * different board. ACC is tied to IG on this bench, so it stays -1 (unwired);
 * a separate ACC needs its own high-side relay (the ULN2803 can only sink).
 *
 * Avoid strapping pins (0, 2, 5, 12, 15) for relays that must stay off at boot;
 * GPIO 12 high at reset changes the flash voltage on classic ESP32.
 *
 * Build: ESP-IDF v5.x  ->  idf.py set-target esp32 && idf.py build flash monitor
 */

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_timer.h"

#define PROTO_VERSION 1
#define UART_PORT UART_NUM_0
#define LINE_MAX 128
#define PRESS_MAX_MS 10000

typedef enum { KIND_POWER, KIND_INPUT, KIND_BUTTON } kind_t;

typedef struct {
    const char *name;
    kind_t kind;
    int pin;            /* -1 = not wired */
    int active_high;    /* 1: GPIO high = ON (relay / ULN2803 input) */
    int state;
    esp_timer_handle_t release_timer;
} channel_t;

/* name, kind, pin, active_high. Order mirrors CHANNELS in esp32_bench.py. */
static channel_t s_channels[] = {
    {"BPLUS",        KIND_POWER,  25, 1},
    {"IG",           KIND_POWER,  26, 1},
    {"ACC",          KIND_POWER,  -1, 1},   /* tied to IG on this bench */
    {"SECURITY",     KIND_INPUT,   4, 1},
    {"ALTERNATOR",   KIND_INPUT,  13, 1},
    {"AIRBAG",       KIND_INPUT,  16, 1},
    {"WASHER_FLUID", KIND_INPUT,  18, 1},
    {"BRAKE_FLUID",  KIND_INPUT,  19, 1},
    {"PKB",          KIND_INPUT,  21, 1},
    {"PSGR_BELT",    KIND_INPUT,  22, 1},
    {"DRVR_BELT",    KIND_INPUT,  23, 1},
    {"HEADLIGHT_LH", KIND_INPUT,  12, 1},   /* strapping pin: must stay low at reset */
    {"HEADLIGHT_RH", KIND_INPUT,  15, 1},
    {"TRIP_RESET",   KIND_BUTTON, 17, 1},
    {"PADDLE_UP",    KIND_BUTTON, 27, 1},
    {"PADDLE_DOWN",  KIND_BUTTON, 32, 1},
    {"BRIGHT_UP",    KIND_BUTTON, 33, 1},
    {"BRIGHT_DOWN",  KIND_BUTTON, 14, 1},
    {"STSW_B_LEFT",  KIND_BUTTON,  5, 1},
    {"STSW_B_RIGHT", KIND_BUTTON,  2, 1},
};
#define N_CHANNELS (sizeof(s_channels) / sizeof(s_channels[0]))

/* Channels sharing one resistor-ladder wire: never assert two at once. */
static const char *s_ladder_groups[][2] = {{"STSW_B_LEFT", "STSW_B_RIGHT"}};
#define N_LADDER (sizeof(s_ladder_groups) / sizeof(s_ladder_groups[0]))

static SemaphoreHandle_t s_out_lock;
static SemaphoreHandle_t s_lock;

static void out(const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf) - 2, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof(buf) - 2) n = sizeof(buf) - 2;
    buf[n++] = '\n';
    xSemaphoreTake(s_out_lock, portMAX_DELAY);
    uart_write_bytes(UART_PORT, buf, n);
    xSemaphoreGive(s_out_lock);
}

static const char *kind_name(kind_t k)
{
    return k == KIND_POWER ? "power" : k == KIND_INPUT ? "input" : "button";
}

static channel_t *find(const char *name)
{
    for (size_t i = 0; i < N_CHANNELS; i++)
        if (strcasecmp(s_channels[i].name, name) == 0) return &s_channels[i];
    return NULL;
}

static void apply(channel_t *c, int on)
{
    if (c->pin < 0) return;
    gpio_set_level(c->pin, c->active_high ? on : !on);
    if (c->state != on) {
        c->state = on;
        out("EVT STATE %s %d", c->name, on);
    }
}

static const char *interlock_other(channel_t *c)
{
    for (size_t g = 0; g < N_LADDER; g++) {
        for (int k = 0; k < 2; k++) {
            if (strcasecmp(s_ladder_groups[g][k], c->name) == 0) {
                channel_t *o = find(s_ladder_groups[g][1 - k]);
                if (o && o->state) return o->name;
            }
        }
    }
    return NULL;
}

static void release_cb(void *arg)
{
    channel_t *c = (channel_t *)arg;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    apply(c, 0);
    xSemaphoreGive(s_lock);
}

static void all_off(void)
{
    for (size_t i = 0; i < N_CHANNELS; i++) {
        if (s_channels[i].release_timer) esp_timer_stop(s_channels[i].release_timer);
        apply(&s_channels[i], 0);
    }
}

static void handle(char *line)
{
    char *argv[4] = {0};
    int argc = 0;
    for (char *tok = strtok(line, " \t\r"); tok && argc < 4; tok = strtok(NULL, " \t\r")) argv[argc++] = tok;
    if (argc == 0) return;
    for (char *p = argv[0]; *p; p++) *p = toupper((unsigned char)*p);

    if (strcmp(argv[0], "PING") == 0) {
        out("OK PONG bench %d", PROTO_VERSION);
    } else if (strcmp(argv[0], "LIST") == 0) {
        char buf[480];
        int n = snprintf(buf, sizeof(buf), "OK LIST");
        for (size_t i = 0; i < N_CHANNELS && n < (int)sizeof(buf) - 32; i++)
            n += snprintf(buf + n, sizeof(buf) - n, " %s:%s:%d", s_channels[i].name,
                          kind_name(s_channels[i].kind), s_channels[i].pin);
        out("%s", buf);
    } else if (strcmp(argv[0], "STATUS") == 0) {
        char buf[480];
        int n = snprintf(buf, sizeof(buf), "OK STATUS");
        xSemaphoreTake(s_lock, portMAX_DELAY);
        for (size_t i = 0; i < N_CHANNELS && n < (int)sizeof(buf) - 24; i++) {
            if (s_channels[i].pin < 0) n += snprintf(buf + n, sizeof(buf) - n, " %s=-", s_channels[i].name);
            else n += snprintf(buf + n, sizeof(buf) - n, " %s=%d", s_channels[i].name, s_channels[i].state);
        }
        xSemaphoreGive(s_lock);
        out("%s", buf);
    } else if (strcmp(argv[0], "SET") == 0) {
        if (argc != 3 || (strcmp(argv[2], "0") && strcmp(argv[2], "1"))) { out("ERR BADARG SET <NAME> <0|1>"); return; }
        channel_t *c = find(argv[1]);
        if (!c) { out("ERR UNKNOWN %s", argv[1]); return; }
        if (c->pin < 0) { out("ERR NOTWIRED %s", c->name); return; }
        if (c->kind == KIND_BUTTON) { out("ERR KIND %s is a button; use PRESS", c->name); return; }
        int on = argv[2][0] == '1';
        xSemaphoreTake(s_lock, portMAX_DELAY);
        out("OK SET %s %d", c->name, on);
        apply(c, on);
        xSemaphoreGive(s_lock);
    } else if (strcmp(argv[0], "PRESS") == 0) {
        if (argc != 3) { out("ERR BADARG PRESS <NAME> <ms>"); return; }
        channel_t *c = find(argv[1]);
        if (!c) { out("ERR UNKNOWN %s", argv[1]); return; }
        if (c->pin < 0) { out("ERR NOTWIRED %s", c->name); return; }
        if (c->kind != KIND_BUTTON) { out("ERR KIND %s is not a button; use SET", c->name); return; }
        int ms = atoi(argv[2]);
        if (ms <= 0 || ms > PRESS_MAX_MS) { out("ERR BADARG ms 1..%d", PRESS_MAX_MS); return; }
        xSemaphoreTake(s_lock, portMAX_DELAY);
        const char *other = interlock_other(c);
        if (other) { xSemaphoreGive(s_lock); out("ERR INTERLOCK %s", other); return; }
        out("OK PRESS %s %d", c->name, ms);
        esp_timer_stop(c->release_timer);
        apply(c, 1);
        esp_timer_start_once(c->release_timer, (uint64_t)ms * 1000ULL);
        xSemaphoreGive(s_lock);
    } else if (strcmp(argv[0], "ALLOFF") == 0) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        out("OK ALLOFF");
        all_off();
        xSemaphoreGive(s_lock);
    } else {
        out("ERR UNKNOWN %s", argv[0]);
    }
}

void app_main(void)
{
    s_out_lock = xSemaphoreCreateMutex();
    s_lock = xSemaphoreCreateMutex();

    for (size_t i = 0; i < N_CHANNELS; i++) {
        channel_t *c = &s_channels[i];
        c->state = 0;
        if (c->pin >= 0) {
            gpio_reset_pin(c->pin);
            gpio_set_direction(c->pin, GPIO_MODE_OUTPUT);
            gpio_set_level(c->pin, c->active_high ? 0 : 1);
        }
        esp_timer_create_args_t args = {.callback = release_cb, .arg = c, .name = c->name};
        esp_timer_create(&args, &c->release_timer);
    }

    uart_config_t cfg = {
        .baud_rate = 115200, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT,
    };
    uart_driver_install(UART_PORT, 1024, 1024, 0, NULL, 0);
    uart_param_config(UART_PORT, &cfg);

    out("EVT READY bench %d", PROTO_VERSION);

    char line[LINE_MAX];
    int len = 0;
    uint8_t ch;
    while (1) {
        if (uart_read_bytes(UART_PORT, &ch, 1, portMAX_DELAY) != 1) continue;
        if (ch == '\n' || ch == '\r') {
            if (len > 0) {
                line[len] = 0;
                handle(line);
                len = 0;
            }
        } else if (len < LINE_MAX - 1) {
            line[len++] = (char)ch;
        } else {
            len = 0;
            out("ERR BADARG line too long");
        }
    }
}
