/*
 * PicoAES - simple side-channel target for the Raspberry Pi Pico / Pico 2.
 *
 * Runs an unoptimised software AES-128 on a single ECB block and toggles a
 * trigger GPIO around each encryption. Controlled over USB CDC serial (and
 * UART0, both via pico_stdio). Commands are line based (CR or LF):
 *
 *   r            reset key / input / trigger to defaults
 *   p <32 hex>   set the 16-byte plaintext input
 *   e <n>        run n encryptions (n = 0: run until any byte is received)
 *   v            print firmware version (git commit hash)
 *
 * See README.md for details.
 */
#include <ctype.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pll.h"
#include "hardware/sync.h"
#include "pico/stdlib.h"

#include "aes.h"
#include "version.h"

/* ---- Configuration -------------------------------------------------- */

/* GPIO used as the scope trigger. Idles high, low while AES is running. */
#ifndef TRIGGER_PIN
#define TRIGGER_PIN 2
#endif

/* Delay between consecutive encryptions in an 'e' run, in milliseconds. */
#ifndef BLOCK_DELAY_MS
#define BLOCK_DELAY_MS 1
#endif

/* Disable interrupts while the trigger is low to reduce trace jitter. */
#ifndef TRIGGER_DISABLE_IRQ
#define TRIGGER_DISABLE_IRQ 1
#endif

/*
 * System (CPU) clock in kHz. A low clock makes traces easier to capture with
 * modest scope bandwidth / sample rates.
 *   12000  clk_sys runs directly from the 12 MHz crystal, PLL_SYS is switched
 *          off (least clock noise / jitter).
 *   other  clk_sys from PLL_SYS; must be exactly reachable, e.g. 20000,
 *          24000, 48000, 125000 (startup panics otherwise).
 * USB and UART stay on the 48 MHz USB PLL, so serial works at any setting.
 */
#ifndef SYS_CLOCK_KHZ
#define SYS_CLOCK_KHZ 12000
#endif

/* Default key and input: FIPS-197 / SP 800-38A F.1.1 test vector. */
#define DEFAULT_KEY   { 0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6, \
                        0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c }
#define DEFAULT_INPUT { 0x6b, 0xc1, 0xbe, 0xe2, 0x2e, 0x40, 0x9f, 0x96, \
                        0xe9, 0x3d, 0x7e, 0x11, 0x73, 0x93, 0x17, 0x2a }
/* Expected ciphertext for the defaults: 3ad77bb40d7a3660a89ecaf32466ef97 */

#define LINE_MAX_LEN 80

/* ---- State ---------------------------------------------------------- */

static const uint8_t default_key[AES_KEY_SIZE] = DEFAULT_KEY;
static const uint8_t default_input[AES_BLOCK_SIZE] = DEFAULT_INPUT;

static aes128_ctx_t aes_ctx;
static uint8_t input[AES_BLOCK_SIZE];
static uint8_t output[AES_BLOCK_SIZE];

/* ---- Helpers -------------------------------------------------------- */

static void print_hex(const uint8_t *buf, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        printf("%02x", buf[i]);
    }
}

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Parse exactly 2 * len hex chars from str. Returns false on bad input. */
static bool parse_hex(const char *str, uint8_t *buf, size_t len)
{
    if (strlen(str) != 2 * len) {
        return false;
    }
    for (size_t i = 0; i < len; i++) {
        int hi = hex_nibble(str[2 * i]);
        int lo = hex_nibble(str[2 * i + 1]);
        if (hi < 0 || lo < 0) {
            return false;
        }
        buf[i] = (uint8_t)((hi << 4) | lo);
    }
    return true;
}

static const char *skip_spaces(const char *s)
{
    while (*s == ' ' || *s == '\t') {
        s++;
    }
    return s;
}

static void setup_clocks(void)
{
#if SYS_CLOCK_KHZ * 1000 == XOSC_HZ
    clock_configure_undivided(clk_sys, CLOCKS_CLK_SYS_CTRL_SRC_VALUE_CLK_REF, 0, XOSC_HZ);
    pll_deinit(pll_sys);
#else
    set_sys_clock_khz(SYS_CLOCK_KHZ, true);
#endif
}

static void reset_state(void)
{
    aes128_init(&aes_ctx, default_key);
    memcpy(input, default_input, sizeof(input));
    memset(output, 0, sizeof(output));
    gpio_put(TRIGGER_PIN, 1);
}

/* One triggered encryption. Trigger goes low before, high after. */
static void encrypt_once(void)
{
#if TRIGGER_DISABLE_IRQ
    uint32_t irq_state = save_and_disable_interrupts();
#endif
    gpio_put(TRIGGER_PIN, 0);
    aes128_encrypt(&aes_ctx, input, output);
    gpio_put(TRIGGER_PIN, 1);
#if TRIGGER_DISABLE_IRQ
    restore_interrupts(irq_state);
#endif
}

/* ---- Commands ------------------------------------------------------- */

static void cmd_reset(void)
{
    reset_state();
    printf("ok\n");
}

static void cmd_set_input(const char *arg)
{
    uint8_t buf[AES_BLOCK_SIZE];

    if (!parse_hex(arg, buf, sizeof(buf))) {
        printf("err: expected 32 hex chars\n");
        return;
    }
    memcpy(input, buf, sizeof(input));
    printf("ok\n");
}

static void cmd_encrypt(const char *arg)
{
    uint32_t count = 1;

    if (*arg != '\0') {
        char *end;
        unsigned long n = strtoul(arg, &end, 0);
        if (*skip_spaces(end) != '\0') {
            printf("err: bad count\n");
            return;
        }
        count = (uint32_t)n;
    }

    if (count == 0) {
        /* Run forever; any received byte stops the loop. */
        uint32_t done = 0;
        while (getchar_timeout_us(0) == PICO_ERROR_TIMEOUT) {
            encrypt_once();
            done++;
            sleep_ms(BLOCK_DELAY_MS);
        }
        printf("stopped after %lu\n", (unsigned long)done);
    } else {
        for (uint32_t i = 0; i < count; i++) {
            encrypt_once();
            if (i + 1 < count) {
                sleep_ms(BLOCK_DELAY_MS);
            }
        }
    }

    printf("c ");
    print_hex(output, sizeof(output));
    printf("\nok\n");
}

static void cmd_version(void)
{
    printf("PicoAES %s\n", PICO_AES_VERSION);
}

static void handle_line(char *line)
{
    line = (char *)skip_spaces(line);
    if (*line == '\0') {
        return;
    }

    char cmd = (char)tolower((unsigned char)line[0]);
    const char *arg = skip_spaces(line + 1);

    switch (cmd) {
    case 'r': cmd_reset();         break;
    case 'p': cmd_set_input(arg);  break;
    case 'e': cmd_encrypt(arg);    break;
    case 'v': cmd_version();       break;
    default:
        printf("err: unknown command '%c'\n", line[0]);
        break;
    }
}

/* ---- Main ----------------------------------------------------------- */

int main(void)
{
    setup_clocks();
    stdio_init_all();

    gpio_init(TRIGGER_PIN);
    gpio_put(TRIGGER_PIN, 1);
    gpio_set_dir(TRIGGER_PIN, GPIO_OUT);

    reset_state();

    char line[LINE_MAX_LEN];
    size_t len = 0;
    bool overflow = false;

    for (;;) {
        int c = getchar();

        if (c == '\r' || c == '\n') {
            if (overflow) {
                printf("err: line too long\n");
            } else {
                line[len] = '\0';
                handle_line(line);
            }
            len = 0;
            overflow = false;
        } else if (len < LINE_MAX_LEN - 1) {
            line[len++] = (char)c;
        } else {
            overflow = true;
        }
    }
}
