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

For power analysis, put a shunt resistor in the supply path. Near-field EM probes over the RP2040/RP2350 package also work well. Either way, the cleanest traces come from powering the board from an external 3.3 V supply, as described next.

## Clean power supply (external 3.3 V)

By default the Pico makes its 3.3 V rail with an on-board switching regulator (SMPS), a buck-boost converter fed from USB 5 V or VSYS. The regulator and its inductor sit right next to the RP2040, so their switching noise shows up both on the supply and in an EM probe. At light load the regulator also runs in **power-save mode** and switches in irregular bursts. These aren't synchronised to the trigger, so they don't average out.

There are two ways to deal with it.

### Option 1: force PWM mode (firmware only, on by default)

GPIO23 controls the regulator's power-save pin. With `SMPS_PWM_MODE=1` (the default), the firmware drives GPIO23 high at startup. That forces fixed-frequency PWM mode, which has lower ripple and no bursts. The regulator still runs, though, so this is a partial fix.

### Option 2: disable the regulator and feed 3.3 V from a clean supply

Pulling `3V3_EN` to GND switches the on-board regulator off. Then feed 3.3 V from a linear bench supply or a low-noise LDO straight into the `3V3` pin.

| Pico pin | Connect to |
|---|---|
| **37 `3V3_EN`** | GND (disables the on-board regulator) |
| **36 `3V3`** | +3.3 V from the clean supply, optionally through a shunt resistor (see below) |
| **38 `GND`** | supply GND |
| 39 `VSYS`, 40 `VBUS` | leave unconnected |
| USB | keep plugged in, for serial and flashing |
| **4 `GP2`**, **3 `GND`** | scope trigger input |

```
                               USB cable to PC (serial + flashing)
                                         ||
                                    +----||----+
                    +---------------|   USB    |---------------+
   UART TX     GP0  |  1  o         +----------+         o 40  |  VBUS      (n/c)
   UART RX     GP1  |  2  o                              o 39  |  VSYS      (n/c)
               GND  |  3  o                              o 38  |  GND    ---------*-------------------.
   trigger --> GP2  |  4  o                              o 37  |  3V3_EN ---------'  tie to GND       |
   to scope         |  5  o                              o 36  |  3V3    ----.                        |
                    |  .                                    .  |             |                        |
                    |  .           Raspberry Pi             .  |          +--+--+                     |
                    |  .           Pico (RP2040)            .  |          |     |  R_shunt            |
                    |  .                                    .  |          |     |  10-22 ohm          |
                    |  .           (top view)               .  |          +--+--+  (optional)         |
                    |                                          |             |                        |
                    +------------------------------------------+             |  <-- scope probe here,  |
                                                                             |      AC coupled        |
                                                             +---------------+------------------------+---+
                                                             |   +3.3 V (up to 3.6 V)            GND       |
                                                             |   clean supply: linear bench supply / LDO  |
                                                             +--------------------------------------------+
```

Notes:

- **USB keeps working.** The RP2040, including its USB PHY, runs from the 3V3 rail, so USB serial and BOOTSEL flashing work as usual. USB 5 V still reaches VSYS through the on-board diode, but only up to the disabled regulator.
- **Supply:** 3.3 V, and **never more than 3.6 V at the `3V3` pin** (the RP2040's I/O supply limit). Use a linear supply, not a switching one, with a current limit of about 100 mA. The board draws a few tens of mA.
- **Order:** turn the 3.3 V supply on before, or together with, plugging in USB. With USB plugged in but no 3.3 V, the board simply stays off.
- **Shunt for power analysis:** put it in the **3.3 V feed** (high side), as drawn. Don't put it in the ground return: with USB connected, the PC's ground and the scope's ground connect to the Pico's GND as well and bypass a ground-side shunt. The supply side of the shunt is clean DC. Probe the Pico side with AC coupling, or measure across the shunt with a differential probe.
- **Voltage drop:** 10 Ω at 30 mA drops 0.3 V. You can raise the supply slightly to compensate, but the `3V3` pin must stay at or below 3.6 V even at idle, when the drop is smallest.
- **On-board decoupling:** the Pico's capacitors on 3V3 smooth out the current spikes you want to see. Removing some of them gives sharper power traces, but that's a hardware mod and is not needed for EM.
- **What remains:** the RP2040's internal core regulator (1.1 V) is still in the path, and on the Pico board it can't be bypassed without hardware surgery. The USB controller also handles the 1 ms USB frame timing in hardware even while interrupts are off, and the 48 MHz USB PLL keeps running. If those show up in your traces, disconnect USB data and use the UART on GP0/GP1 instead.
- The Pico 2 uses the same pin numbers. Check the power section of the Pico / Pico 2 datasheet before modifying the board.

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
| `SMPS_PWM_MODE`       | `1`     | Drive GPIO23 high to force the on-board regulator into PWM mode (see [Clean power supply](#clean-power-supply-external-33-v)) |

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
