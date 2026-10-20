# PicoAES – side-channel target for the Raspberry Pi Pico

PicoAES is a small side-channel analysis (SCA) target for the Raspberry Pi Pico (RP2040) and Pico 2 (RP2350). It runs a deliberately **unoptimised software AES-128** on a single ECB block. A GPIO trigger goes low before each encryption and high after it, so you can capture power or EM traces with a scope. You control it with simple text commands over serial.

> ⚠️ This is a deliberately leaky implementation for training and experiments. Do not use it to protect anything.

## Features

- Textbook byte-oriented AES-128: S-box lookups, explicit ShiftRows, `xtime()` MixColumns. No T-tables and no countermeasures.
- `aes.c` is always compiled with `-O0`, whatever the build type.
- Runs from SRAM (`copy_to_ram` binary), so XIP flash cache misses don't add jitter.
- Interrupts are disabled while the trigger is low (configurable).
- Serial over **USB CDC** and **UART0** (GP0 TX / GP1 RX, 115200 8N1), both at the same time.
- The firmware version is the git commit hash, generated at build time.

## Hardware / pinout

| Signal  | Pico pin           | Notes                                       |
|---------|--------------------|---------------------------------------------|
| Trigger | **GP2** (pin 4)    | Idles **high**, **low** during each AES block |
| UART TX | GP0 (pin 1)        | Optional; USB CDC works too                 |
| UART RX | GP1 (pin 2)        |                                             |
| GND     | e.g. pin 3         | Scope / UART ground                         |

Set the scope to trigger on the **falling edge** of GP2. The low pulse covers exactly one AES encryption: the initial AddRoundKey, 10 rounds, and the copy of the output.

For power analysis, the usual approach is a shunt resistor in the supply path. On the Pico you can feed 3.3 V straight into the `3V3` pin (with `3V3_EN` pulled to GND to disable the on-board buck converter) through a shunt, or measure across a resistor in the VSYS/GND path. Near-field EM probes over the RP2040/RP2350 package also work well.

## Serial protocol

Commands are single lines ending in CR, LF or CRLF. Commands are case-insensitive, and the space after the command letter is optional. Responses end with `ok`, or with one line starting `err:`.

| Command        | Description |
|----------------|-------------|
| `r`            | Reset to defaults: default key, default input, output cleared, trigger high. |
| `p <32 hex>`   | Set the 16-byte plaintext input, e.g. `p 00112233445566778899aabbccddeeff`. |
| `e <n>`        | Run `n` encryptions of the current input. The trigger goes low/high around each block, with `BLOCK_DELAY_MS` between blocks. Prints the ciphertext as `c <32 hex>`, then `ok`. `e` without a number runs one encryption. |
| `e 0`          | Run forever. Send **any byte** to stop, then it prints `stopped after <count>`, the ciphertext, and `ok`. |
| `v`            | Print the version: `PicoAES <git hash>`. The hash gets a `-dirty` suffix if the tree had uncommitted changes, or is `unknown` if the source wasn't built from a git checkout. |

Every block in an `e` run encrypts the **same** input. For CPA/DPA, send a new `p` and then `e 1` for each trace.

### Defaults

The defaults are the FIPS-197 / NIST SP 800-38A F.1.1 test vector:

| | Value |
|---|---|
| Key        | `2b7e151628aed2a6abf7158809cf4f3c` |
| Input      | `6bc1bee22e409f96e93d7e117393172a` |
| Ciphertext | `3ad77bb40d7a3660a89ecaf32466ef97` |

The key is fixed at compile time (`DEFAULT_KEY` in [src/main.c](src/main.c)).

### Example session

```
> v
PicoAES 3bd2039e
> e 1
c 3ad77bb40d7a3660a89ecaf32466ef97
ok
> p 00112233445566778899aabbccddeeff
ok
> e 100
c 8df4e9aac5c7573a27d8d055d6e4d64b
ok
> r
ok
```

### Python capture loop (sketch)

```python
import os, serial

s = serial.Serial("/dev/ttyACM0", 115200, timeout=1)

def cmd(line):
    s.write((line + "\n").encode())
    out = []
    while True:
        l = s.readline().decode().strip()
        out.append(l)
        if l == "ok" or l.startswith("err"):
            return out

cmd("r")
for _ in range(1000):
    pt = os.urandom(16)
    cmd("p " + pt.hex())
    scope.arm()                       # your scope API
    ct = bytes.fromhex(cmd("e 1")[0].split()[1])
    trace = scope.capture()
    # store (pt, ct, trace) ...
```

## Configuration

These compile-time options live at the top of [src/main.c](src/main.c). You can also override them from CMake:

| Define                | Default | Description |
|-----------------------|---------|-------------|
| `TRIGGER_PIN`         | `2`     | GPIO used as the trigger output |
| `BLOCK_DELAY_MS`      | `1`     | Delay between consecutive blocks in an `e` run, in ms |
| `TRIGGER_DISABLE_IRQ` | `1`     | Disable interrupts while the trigger is low (less jitter) |
| `SYS_CLOCK_KHZ`       | `12000` | CPU clock in kHz while the AES runs (see below) |
| `CLOCK_SETTLE_US`     | `10`    | Wait after switching to the crystal clock, before the trigger goes low |

```sh
cmake -B build -DTRIGGER_PIN=15 -DBLOCK_DELAY_MS=5 -DSYS_CLOCK_KHZ=48000
```

### CPU clock

PicoAES runs the AES at a deliberately low clock. That stretches each block out in time, so you can capture it with modest scope bandwidth and sample rates.

- **`12000` (default):** the CPU normally runs at the SDK default clock (125 MHz on RP2040), so USB works. For each encryption it switches `clk_sys` to the 12 MHz crystal (`clk_ref`) with interrupts disabled:

  1. switch `clk_sys` to the crystal, wait until the switch has completed
  2. wait `CLOCK_SETTLE_US` (10 µs) so the supply settles after the drop in current
  3. trigger **low**, AES, trigger **high**
  4. switch `clk_sys` back to the PLL

  Both clock switches are outside the trigger window, and the whole encryption runs from the crystal.
- **`48000` or higher:** `clk_sys` comes from the system PLL the whole time, without switching. The value must be reachable exactly, e.g. `48000` or `125000`. If it isn't, the firmware panics at startup.

Any other value fails to compile. USB device mode is unreliable when `clk_sys` is below 48 MHz (the board stops enumerating), which is why 12 MHz is only used inside the encryption window.

The UART's peripheral clock runs from the 48 MHz USB PLL, so its baud rate doesn't depend on the CPU clock.

## Building

### Prerequisites

- CMake ≥ 3.13 and a build tool (Ninja or Make)
- Arm GNU toolchain (`arm-none-eabi-gcc`). On Debian/Ubuntu: `sudo apt install cmake ninja-build gcc-arm-none-eabi libnewlib-arm-none-eabi build-essential`
- [Raspberry Pi Pico SDK](https://github.com/raspberrypi/pico-sdk) 2.x, with the `tinyusb` submodule initialised (needed for USB serial)

```sh
git clone --branch 2.1.1 https://github.com/raspberrypi/pico-sdk ~/pico-sdk
git -C ~/pico-sdk submodule update --init lib/tinyusb
export PICO_SDK_PATH=~/pico-sdk
```

Alternatively, pass `-DPICO_SDK_FETCH_FROM_GIT=on` and CMake will download the SDK for you.

### Build

```sh
cd targets/pico_aes
cmake -B build -G Ninja                    # Pico (RP2040), the default
cmake --build build
```

For a Pico 2 (RP2350), use a separate build directory:

```sh
cmake -B build-pico2 -G Ninja -DPICO_BOARD=pico2
cmake --build build-pico2
```

The output is `build/pico_aes.uf2`, along with `.elf`, `.bin`, `.hex` and `.dis` (disassembly, useful for matching trace features to code).

The first build also compiles `picotool` from source if it isn't installed, which takes a minute.

The version hash is regenerated on every build, so you don't need to re-run cmake after committing.

### Flashing

1. Hold **BOOTSEL** while plugging in the Pico. It mounts as a USB drive (`RPI-RP2` or `RP2350`).
2. Copy `build/pico_aes.uf2` onto that drive. The board reboots into PicoAES.

Or with picotool: `picotool load -x build/pico_aes.uf2 -f`

### Connecting

```sh
picocom /dev/ttyACM0 --omap crlf --echo    # USB CDC (Linux)
# or
screen /dev/ttyACM0 115200
```

There is no local echo or banner; type `v` and press Enter to check that it's alive.

## Notes for trace capture

- Each `e` block re-encrypts the stored input. The key schedule is computed once (on boot and on `r`), not inside the triggered window.
- USB CDC servicing runs in interrupt context. With `TRIGGER_DISABLE_IRQ=1` it can't land inside a trigger window, but it can still run during the inter-block delay.
- For the cleanest traces, use UART instead of USB and/or a separate supply. You can also compare with `TRIGGER_DISABLE_IRQ=0` to see what interrupt jitter looks like.
- At the default 12 MHz, one encryption takes roughly 10× longer than at the SDK's default 125 MHz. Use `SYS_CLOCK_KHZ` to trade capture length against bandwidth.
- The trigger window contains only the AES at 12 MHz. The clock switches and the settle time happen just before the trigger goes low and just after it goes high, so keep a negative capture delay (pre-trigger) shorter than `CLOCK_SETTLE_US` if you don't want the switch in your traces.

## Layout

```
pico_aes/
├── CMakeLists.txt
├── pico_sdk_import.cmake   # locates / fetches the Pico SDK
├── cmake/version.cmake     # generates version.h from git
└── src/
    ├── main.c              # serial command handler, trigger, config
    ├── aes.c               # unoptimised AES-128 encrypt
    └── aes.h
```
