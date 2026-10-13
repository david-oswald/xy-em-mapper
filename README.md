# xy-em-mapper

Tools for recording large side-channel (power / EM) trace sets and for moving a
probe over a target with a GRBL-based XY(Z) CNC stage.

Two parts:

- **`scopes/`**: a small oscilloscope abstraction for fast, segmented trace
  capture into numpy arrays. Backends: PicoScope 6000E series (e.g. 6424E).
  Rigol is planned.
- **`cnc/`**: a serial controller for GRBL-like CNC boards, used to position
  the EM probe.

It runs on Linux, Windows (natively or in WSL2) and macOS.

## Repository layout

```
scope.py              CLI: list oscilloscopes, record a test trace
map.py                CLI: trace a square toolpath with the CNC stage
scopes/
  base.py             Scope interface (Scope, ChannelConfig, TriggerConfig, ...)
  picoscope6000e.py   PicoScope 6000E backend (ps6000a driver, via ctypes)
  __init__.py         backend registry: get_backend("picoscope6000e")
cnc/
  controller.py       CNCController for GRBL-like boards over serial
requirements.txt
```

---

## Installation

You need:

1. Python 3.10 or newer
2. The Python packages in `requirements.txt` (`numpy`, `pyserial`)
3. For PicoScope capture, the vendor **PicoSDK**. pip can't install it; see
   [Install PicoSDK](#3-install-picosdk-for-picoscope-capture) below.

### 1. Get the code

```bash
git clone <this repository> xy-em-mapper
cd xy-em-mapper
```

### 2. Create a virtual environment and install Python packages

**Linux / macOS / WSL**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell refuses to run the activation script, allow it for your user:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Re-activate the environment (`source .venv/bin/activate` or
`.venv\Scripts\Activate.ps1`) in every new terminal before running the tools.

### 3. Install PicoSDK (for PicoScope capture)

The PicoScope backend loads Pico's `ps6000a` driver library directly. Install
PicoSDK for your platform, including the **ps6000a** driver. The 6000E series
uses `ps6000a`; the older non-E 6000 series uses `ps6000`, which this backend
does not support.

Downloads and the current install instructions:
<https://www.picotech.com/downloads> (choose your PicoScope 6000E model, then
"PicoSDK"). Package names and repository URLs below match Pico's instructions
at the time of writing. If a step fails, check Pico's page first.

#### Linux (Ubuntu / Debian)

Add Pico's package repository and install the 6000E driver:

```bash
sudo mkdir -p /usr/share/keyrings
wget -qO - https://labs.picotech.com/Release.gpg.key \
  | sudo gpg --dearmor -o /usr/share/keyrings/picotech.gpg
echo "deb [signed-by=/usr/share/keyrings/picotech.gpg] https://labs.picotech.com/picoscope7/debian/ picoscope main" \
  | sudo tee /etc/apt/sources.list.d/picoscope7.list
sudo apt update
sudo apt install libps6000a
```

This installs the library to `/opt/picoscope/lib/libps6000a.so`, along with
udev rules for USB access. To use the scope without `sudo`, add yourself to
the `pico` group, then log out and back in:

```bash
sudo usermod -a -G pico $USER
```

For other distributions (Fedora, openSUSE, Arch), see Pico's Linux page. RPM
packages are available, and the AUR has community packages.

#### Windows (native)

1. Download the **64-bit PicoSDK** installer (it must match 64-bit Python)
   and run it.
2. The DLLs are installed to `C:\Program Files\Pico Technology\SDK\lib`,
   where the backend finds them automatically.
3. Connect the scope. The USB driver comes with the SDK.

Running natively on Windows is the simplest and fastest Windows option for
USB scopes. WSL2 also works but needs USB forwarding (next section).

#### Windows with WSL2

WSL2 doesn't see USB devices by default. Forward the scope (and the CNC's
USB-serial adapter) from Windows with **usbipd-win**:

1. In Windows, install usbipd-win once (in PowerShell):
   ```powershell
   winget install usbipd
   ```
2. With the scope plugged in, in an **administrator** PowerShell:
   ```powershell
   usbipd list                         # find the BUSID of "PicoScope 6000 Series"
   usbipd bind --busid <BUSID>         # once per device
   usbipd attach --wsl --busid <BUSID> # after every replug / WSL restart
   ```
3. Inside WSL, check that the device is visible:
   ```bash
   lsusb | grep -i pico   # expect "0ce9:... Pico Technology"
   ```
4. Install PicoSDK inside WSL as described under
   [Linux](#linux-ubuntu--debian).

WSL often doesn't run udev, so the `pico` group permissions may not apply. If
opening the scope fails with a permissions or "not found" error while `lsusb`
shows it, run `sudo service udev restart`, re-attach the device, or run the
tool once with `sudo` to confirm the cause. Forwarded USB is slower than
native USB, so for maximum capture throughput run natively on Windows.

#### macOS

1. Download and install the **PicoSDK for macOS** package from Pico's
   download page.
2. The backend searches `/Library/Frameworks/PicoSDK.framework/Libraries/...`
   and the PicoScope 7 app bundle.
3. On Apple Silicon, check that your PicoSDK release includes a native arm64
   `libps6000a`. If the library fails to load with an architecture error, you
   need an arm64 build of the SDK or an x86_64 Python running under Rosetta.

#### Non-standard install location

If the SDK is somewhere else, point the backend at the directory that contains
`libps6000a.so` / `ps6000a.dll` / `libps6000a.dylib`:

```bash
export PICOSDK_LIB_DIR=/path/to/picosdk/lib      # Linux / macOS / WSL
$env:PICOSDK_LIB_DIR = "D:\PicoSDK\lib"         # Windows PowerShell
```

### 4. Verify the oscilloscope setup

Connect the scope (USB 3 port, external power supply connected) and run:

```bash
python scope.py picoscope6000e
```

Expected output:

```
Devices:
  picoscope6000e:JY123/0001
Opened picoscope6000e:JY123/0001 (6424E)
```

| Message | Cause / fix |
|---|---|
| `Could not load the PicoSDK ps6000a library` | SDK not installed, wrong architecture, or set `PICOSDK_LIB_DIR` |
| `No picoscope6000e devices found` | Not plugged in or powered, not forwarded to WSL, missing USB permissions, or the scope is open in PicoScope 7 (close it) |
| `PICO_POWER_SUPPLY_NOT_CONNECTED` | Connect the scope's external power supply |
| Warning about a non-USB3 port | Works, but transfers are much slower; use a USB 3 port |

### 5. Set up the CNC serial port

The CNC board shows up as a serial port:

| OS | Typical port name | Notes |
|---|---|---|
| Linux / WSL | `/dev/ttyUSB0` or `/dev/ttyACM0` | Add yourself to `dialout`: `sudo usermod -a -G dialout $USER`, then log out and back in |
| Windows | `COM3`, `COM4`, ... | See Device Manager → Ports (COM & LPT) |
| macOS | `/dev/cu.usbserial-*` or `/dev/cu.usbmodem*` | `ls /dev/cu.*` |

In WSL, forward the USB-serial adapter with `usbipd` in the same way as the
scope.

---

## Recording traces: `scope.py`

`scope.py` lists the devices for a backend, opens the first one (or
`--serial`), and optionally records traces on one channel.

```bash
# List and open only
python scope.py picoscope6000e

# Free-running capture: channel A, +-200 mV, 1.25 GSa/s, 20k samples
python scope.py picoscope6000e --record --channel A --range 0.2 --rate 1.25e9 --samples 20000

# Triggered on a rising edge on channel B at 1.0 V, 100 ns of pre-trigger,
# auto-trigger after 100 ms, saved to disk
python scope.py picoscope6000e --record --channel A --range 0.05 --coupling ac \
    --trigger-channel B --trigger-range 5 --trigger-level 1.0 --trigger-edge rising \
    --auto-trigger 0.1 --delay -1e-7 --out trace.npy

# 1000 triggered segments in one rapid-block capture
python scope.py picoscope6000e --record --trigger-channel A --trigger-level 0.02 \
    --segments 1000 --out traces.npy
```

| Option | Meaning |
|---|---|
| `--serial` | Open a specific device instead of the first one |
| `--channel` | Input channel to record (`A`–`D` on a 6424E) |
| `--range` | Input range in ±V: 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20 (50 Ω coupling allows up to 5 V) |
| `--coupling` | `dc` (1 MΩ), `ac`, or `dc50` (50 Ω) |
| `--offset` | Analogue offset in V, added to the signal before digitizing |
| `--rate` | Sample rate in Sa/s. The nearest achievable rate is used and printed |
| `--samples` | Samples per trace |
| `--delay` | First sample relative to the trigger in seconds. Positive = trigger delay, negative = pre-trigger |
| `--segments` | Number of triggered traces captured in one go |
| `--trigger-channel` | Trigger source (captured as well). Without it the scope free-runs |
| `--trigger-level`, `--trigger-edge` | Edge trigger level in V, `rising` or `falling` |
| `--trigger-range` | Range of the trigger channel if it differs from `--channel` |
| `--auto-trigger` | Fire after this many seconds without a trigger. Such traces are reported |
| `--timeout` | Give up if the capture doesn't finish in this many seconds |
| `--out` | Write raw samples to a `.npy` file and metadata to a `.json` file next to it |

### Output format

- `trace.npy`: **raw ADC values** (`int8` on the 6424E, which is an 8-bit
  scope), shape `(n_samples,)` for one segment or `(n_segments, n_samples)`.
  Load with `np.load("trace.npy")`, or `np.load(..., mmap_mode="r")` for large
  files.
- `trace.json`: sample interval, trigger delay in samples, dtype, and per
  channel `volts_per_count` / `offset_v`. Convert to volts with:

  ```python
  volts = raw.astype(np.float32) * volts_per_count + offset_v
  ```

## Using the scope API from Python

For real measurements, call the API directly so that capturing and storing can
run in a loop:

```python
import numpy as np
from scopes import get_backend, ChannelConfig, AcquisitionConfig, TriggerConfig, Coupling, Edge

Pico = get_backend("picoscope6000e")
print(Pico.list_devices())

with Pico.open() as scope:                        # first device, or Pico.open("JY123/0001")
    info = scope.configure(
        channels=[
            ChannelConfig("A", range_v=0.05, coupling=Coupling.AC),             # EM probe
            ChannelConfig("B", range_v=5.0, coupling=Coupling.DC),              # trigger
        ],
        acquisition=AcquisitionConfig(sample_rate_hz=1.25e9, n_samples=50_000, delay_s=-50e-9),
        trigger=TriggerConfig("B", level_v=1.5, edge=Edge.RISING, auto_trigger_s=0.1),
    )

    n_seg = 1000
    buffers = scope.allocate(n_seg)                # {"A": int8 (1000, 50000), "B": ...}
    for batch in range(100):
        result = scope.capture(buffers, timeout_s=10)   # arm + wait + fetch
        good = ~result.auto_triggered
        # store buffers["A"][good] and info.metadata() in your container ...
```

To overlap your own work (e.g. sending plaintexts to the target) with the
capture, use the steps separately:

```python
scope.arm(n_seg)        # scope now waits for n_seg triggers
...                     # drive the target
scope.wait(timeout_s=10)
result = scope.fetch(buffers)
scope.disarm()          # abort early; already captured segments stay fetchable
```

Notes:

- **Fetch is zero-copy.** The driver writes straight into the numpy arrays.
  Reusing the same buffers on every iteration skips per-segment buffer
  registration. Buffers can also be views into a `np.lib.format.open_memmap`
  file or another C-contiguous array.
- **`info.max_segments`** is the most segments that fit in scope memory at the
  current record length and channel count.
- **Auto-trigger detection.** `result.auto_triggered` marks segments captured
  by the auto-trigger timeout rather than a real trigger.
  `result.auto_trigger_detection` says how this was determined:
  - `driver`: the PicoSDK reports it per segment (newer SDKs only).
  - `data`: the trigger edge is searched for around the trigger point. This
    needs a small negative `delay_s` (pre-trigger samples) and the trigger
    channel in the fetched buffers.
  - `timing`: single-segment captures that took at least the auto-trigger
    time.
  - `none`: no auto-trigger configured, or detection not possible (a warning
    is printed).
- **`result.overrange[ch]`** marks segments where the input exceeded the range.

---

## CNC stage: `cnc/` and `map.py`

`cnc.CNCController` drives GRBL-compatible boards over serial with plain
G-code:

- On connect it waits for the board to boot (2 s), unlocks it (`$X`), and sets
  absolute positioning (`G90`) in millimetres (`G21`).
- `move_to(x, y, z, feed_rate)` sends `G1`, and `rapid_to(x, y, z)` sends `G0`.
  Both wait for GRBL's `ok` and raise `CNCCommandError` on `error`/`ALARM`.
- `wait_until_idle(timeout_s)` polls the status report (`?`) until the machine
  reports `Idle`.

```python
from cnc import CNCConfig, CNCController

with CNCController(CNCConfig(port="/dev/ttyUSB0")) as cnc:
    cnc.rapid_to(z=5)
    cnc.rapid_to(x=10, y=10)
    cnc.move_to(z=0, feed_rate=300)
    cnc.wait_until_idle()
```

### `map.py`: trace a square

`map.py` moves the probe along the outline of a square, stopping at evenly
spaced points on each edge. It is useful for checking the stage and the
coordinate system.

```bash
python map.py --port /dev/ttyUSB0 --x0 0 --y0 0 --side-mm 40 --steps 10 --z-work 0 --z-safe 5 --feed 600
```

| Option | Default | Meaning |
|---|---|---|
| `--port` | required | Serial port (`/dev/ttyUSB0`, `COM3`, ...) |
| `--baudrate` | 115200 | GRBL baud rate |
| `--timeout` | 1.0 | Serial read timeout in s |
| `--x0`, `--y0` | 0 | Start corner in mm |
| `--side-mm` | 20 | Side length in mm |
| `--steps` | 5 | Segments per edge |
| `--z-work`, `--z-safe` | 0, 5 | Z height while tracing / while travelling |
| `--feed` | 600 | Feed rate in mm/min |
| `--rapid-trace` | off | Use `G0` instead of `G1` along the edges |

**Before the first run**, home or zero the machine (for example `$H`, or
`G10 L20 P1 X0 Y0 Z0` at the start position in a GRBL terminal). Check that
`--z-safe` clears the target. Coordinates are absolute in the current work
coordinate system.

---

## Troubleshooting

- **`ModuleNotFoundError: numpy` / `serial`**: the virtual environment isn't
  active, or `pip install -r requirements.txt` wasn't run in it.
- **Scope works in PicoScope 7 but not here**: only one program can open the
  scope at a time. Close PicoScope 7.
- **Slow captures**: use a USB 3 port, run natively rather than in WSL,
  capture many segments per `arm()`, and reuse fetch buffers.
- **`Permission denied: '/dev/ttyUSB0'`**: add yourself to `dialout` (Linux)
  and log in again.
- **CNC `No response for command`**: wrong port or baud rate, or the board is
  in an alarm state. Connect with a serial terminal and check that it prints
  the GRBL banner.
