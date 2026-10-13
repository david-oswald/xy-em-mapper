# xy-em-mapper

Tools for recording large side-channel (power / EM) trace sets and for moving a
probe over a target with a GRBL-based XY(Z) CNC stage.

Three parts:

- **`em-mapper.py` / `mapper/`**: moves the probe over an XY grid and captures
  traces at every point (e.g. fixed-vs-random), writing everything into one
  HDF5 file.
- **`scopes/`**: a small oscilloscope abstraction for fast, segmented trace
  capture into numpy arrays. Backends: PicoScope 6000E series (e.g. 6424E).
  Rigol is planned.
- **`cnc/`**: a serial controller for GRBL-like CNC boards, used to position
  the EM probe.

It runs on Linux, Windows (natively or in WSL2) and macOS.

## Repository layout

```
em-mapper.py          CLI: capture traces on an XY grid from a JSON config
scope.py              CLI: list oscilloscopes, record a test trace
xy-demo.py            CLI: trace a square toolpath with the CNC stage
mapper/
  config.py           JSON config parsing and the measurement grid
  strategies.py       what to send per trace (fixed_vs_random, plug-ins)
  targets.py          start/next/end hooks into the device under test
  storage.py          HDF5 output file
  runner.py           the mapping loop
scopes/
  base.py             Scope interface (Scope, ChannelConfig, TriggerConfig, ...)
  picoscope6000e.py   PicoScope 6000E backend (ps6000a driver, via ctypes)
  __init__.py         backend registry: get_backend("picoscope6000e")
cnc/
  controller.py       CNCController for GRBL-like boards over serial
examples/
  em-mapper.json      example measurement config
requirements.txt
```

---

## Installation

You need:

1. Python 3.10 or newer
2. The Python packages in `requirements.txt` (`numpy`, `h5py`, `pyserial`)
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

## EM mapping: `em-mapper.py`

`em-mapper.py` reads a JSON measurement config and, for every point of an XY
grid:

1. moves the stage to the point (optionally lifting to a safe Z in between)
2. asks the **strategy** for the plaintexts and class labels of the point's
   traces (e.g. half fixed, half random, interleaved)
3. captures `traces_per_point` traces, calling the **target** hooks
   `start` / `next` / `end` so the device under test produces the triggers
4. re-captures traces that were auto-triggered or missing (up to `max_retries`)
5. writes the traces, plaintexts, labels and the x/y/z position of every trace
   to one HDF5 file

```bash
python em-mapper.py examples/em-mapper.json --dry-run     # check config, grid size, file size
python em-mapper.py examples/em-mapper.json -o run1.h5    # measure
python em-mapper.py examples/em-mapper.json -o run1.h5 --resume   # continue after Ctrl-C / error
```

The output file is created up front and each point is marked done once it is
written. After an interruption, `--resume` skips the completed points. It
refuses to continue if the config file has changed since the run started.

### Configuration

See [examples/em-mapper.json](examples/em-mapper.json). Unknown keys are
rejected, so typos cause an error rather than silently falling back to a
default.

```json
{
  "scope": {
    "backend": "picoscope6000e",
    "serial": null,
    "channels": [
      {"name": "A", "range_v": 0.05, "coupling": "ac", "offset_v": 0.0},
      {"name": "B", "range_v": 5.0, "coupling": "dc"}
    ],
    "sample_rate_hz": 1.25e9,
    "n_samples": 5000,
    "delay_s": -1e-8,
    "trigger": {"source": "B", "level_v": 1.5, "edge": "rising", "auto_trigger_s": 0.1},
    "segments_per_arm": null,
    "timeout_s": 10,
    "max_retries": 3
  },
  "stage": {"port": "/dev/ttyUSB0", "baudrate": 115200, "feed_mm_min": 600, "z_safe": 5.0, "settle_s": 0.2},
  "grid": {
    "x_start": 0.0, "x_end": 10.0, "x_step": 0.5,
    "y_start": 0.0, "y_end": 10.0, "y_step": 0.5,
    "z": 0.0, "order": "serpentine"
  },
  "traces_per_point": 1000,
  "strategy": {"name": "fixed_vs_random", "fixed_plaintext": "da39a3ee5e6b4b0d3255bfef95601890", "order": "random", "seed": 1},
  "target": {"name": "none"},
  "output": "em-map.h5",
  "compression": null
}
```

**`scope`**: the same settings as `scope.py` (see above). In addition:

| Key | Default | Meaning |
|---|---|---|
| `channels[].coupling` | `"dc"` | `"ac"`, `"dc"` (1 MΩ) or `"dc50"` (50 Ω) |
| `channels[].bandwidth_limit_hz` | `null` | e.g. `20e6` for the 20 MHz limiter |
| `delay_s` | `0` | first sample relative to the trigger; negative = pre-trigger |
| `trigger` | `null` | `null` = free-running |
| `segments_per_arm` | `null` | traces captured per scope arm; `null` = all traces of a point at once (capped by scope memory) |
| `timeout_s` | `10` | per arm; traces missing after the timeout are re-captured |
| `max_retries` | `3` | re-capture rounds for auto-triggered or missing traces; traces still bad afterwards are kept and flagged in `auto_triggered` |

**`stage`**: CNC serial settings, or `null` to run without moving anything
(positions are still recorded, which is useful for testing). `z_safe`: lift
to this Z before every XY move; `null` moves at the working Z. `settle_s`:
wait after each move so vibrations die down. Also: `timeout` (serial),
`move_timeout_s`.

**`grid`**: `x_start`…`x_end` and `y_start`…`y_end` are inclusive, with step
size `x_step`/`y_step` (> 0; the direction follows start → end). `z` is the
working height. `order`: `"serpentine"` (every other row reversed, shortest
travel) or `"raster"`. All values are in mm, in the CNC's work coordinates.

**`strategy`** / **`target`**: `{"name": ..., <constructor parameters>}`, see
below.

**`compression`**: `null` (fastest), `"lzf"` (fast, moderate) or `"gzip"`.
Raw 8-bit EM traces often compress poorly, so leave it off unless disk space
matters more than speed.

### Strategies: what is sent at each point

A strategy returns a `Plan` per point: a plaintext and a class label for every
trace, in capture order.

**`fixed_vs_random`** (built in) splits the traces 50/50 between one fixed
plaintext (label 0, `"fixed"`) and fresh random plaintexts (label 1,
`"random"`):

| Parameter | Default | Meaning |
|---|---|---|
| `fixed_plaintext` | required | hex string; its length sets the plaintext size |
| `order` | `"random"` | `"random"`: fixed/random shuffled; `"alternate"`: F, R, F, R, ... |
| `seed` | `null` | makes plaintexts and order reproducible (seeded per point) |

To add a strategy, subclass `mapper.Strategy` and point the config at it:

```python
# my_strategies.py
import numpy as np
from mapper import Plan, Strategy

class AllRandom(Strategy):
    label_names = ("random",)

    def __init__(self, plaintext_bytes: int = 16):
        self.plaintext_bytes = plaintext_bytes

    def plan(self, point, n_traces):
        pts = np.random.default_rng().integers(0, 256, (n_traces, self.plaintext_bytes), dtype=np.uint8)
        return Plan(pts, np.zeros(n_traces, dtype=np.uint8))
```

```json
"strategy": {"name": "my_strategies.py:AllRandom", "plaintext_bytes": 16}
```

A `file.py:Class` path is resolved relative to the config file.
`module:Class` imports a module from the Python path.

### Targets: hooks into the device under test

At every point the mapper calls:

| Hook | When | Typical use |
|---|---|---|
| `start(point, plan)` | once, before the scope is armed | reset the target, upload the plaintext list or a seed |
| `next(i, plan)` | once per trace, in order, after the scope is armed for the batch containing trace `i` | send plaintext `i` (one trigger), or nothing |
| `end(point, plan)` | once, after all traces | read back results |

Plaintexts: `next` may return the plaintext actually used (`bytes`), and
`end` may return all of them as an `(n, plaintext_bytes)` array, e.g. when
the target derives them itself. Otherwise the plan's plaintexts are stored.
Auto-triggered traces are re-captured by calling `next` again for their
indices.

The built-in target `"none"` does nothing. Use it when the device triggers by
itself, or for testing with a free-running or auto-triggered scope.

Example for firmware that runs one encryption per received plaintext:

```python
# my_target.py
import serial
from mapper import Target

class SerialAES(Target):
    def __init__(self, port: str, baudrate: int = 115200):
        self.ser = serial.Serial(port, baudrate, timeout=1)

    def next(self, i, plan):
        self.ser.write(b"p" + plan.plaintexts[i].tobytes())
        self.ser.read(1)   # wait for the ack, so the trigger for trace i has happened
        return None        # the planned plaintext was used

    def close(self):
        self.ser.close()
```

```json
"target": {"name": "my_target.py:SerialAES", "port": "/dev/ttyACM0"}
```

For firmware that produces all `n` triggers from **one command**, send the
command in `next` when `i == 0` and do nothing for the other indices. Keep
`segments_per_arm` at `null` so that all traces of a point are armed at once,
and make sure `traces_per_point` fits in scope memory. The scope prints its
segment limit (`max segments`) in `scope.py --record` output. If the firmware
chooses the plaintexts itself, return them from `next` or `end`.

### Output file

One HDF5 file per run. Every per-trace dataset has one row per trace, over all
points. The traces of point `p` are the contiguous rows
`points/start[p] : points/start[p] + points/count[p]`.

| Dataset | Shape / type | Content |
|---|---|---|
| `traces/<ch>` | `(N, n_samples)` int8 (int16 at >8 bit) | raw ADC values |
| `plaintext` | `(N, plaintext_bytes)` uint8 | plaintext of each trace |
| `label` | `(N,)` uint8 | class, index into attribute `label_names` (`fixed`, `random`) |
| `x`, `y`, `z` | `(N,)` float64 | probe position of each trace in mm |
| `point` | `(N,)` uint32 | grid point index |
| `auto_triggered` | `(N,)` bool | still auto-triggered or missing after all retries; exclude these |
| `overrange/<ch>` | `(N,)` bool | input exceeded the range |
| `points/{x,y,z,start,count,done,time}` | `(P,)` | per-point table; `done` is set once a point is complete |

Attributes: `config` (the JSON config), `scope` (JSON: sample interval,
trigger delay, per-channel `volts_per_count` / `offset_v`), `strategy`,
`target`, `label_names`, `channels`, `traces_per_point`, `created`.

Reading it:

```python
import json
import h5py
import numpy as np

with h5py.File("em-map.h5", "r") as f:
    scope = json.loads(f.attrs["scope"])
    done = f["points/done"][:]
    for p in np.flatnonzero(done):
        a = int(f["points/start"][p]); b = a + int(f["points/count"][p])
        traces = f["traces/A"][a:b]                    # raw int8
        labels = f["label"][a:b]
        ok = ~f["auto_triggered"][a:b]
        fixed, rand = traces[ok & (labels == 0)], traces[ok & (labels == 1)]
        # ... e.g. Welch t-test per sample -> leakage map at (points/x[p], points/y[p])

    s = scope["scaling"]["A"]
    volts = traces.astype(np.float32) * s["volts_per_count"] + s["offset_v"]
```

The file can also be read with `h5py` in Python, MATLAB (`h5read`), Julia
(HDF5.jl), and `h5dump` / HDFView. A point's traces are stored contiguously,
so reading a single point is fast.

---

## CNC stage: `cnc/` and `xy-demo.py`

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

### `xy-demo.py`: trace a square

`xy-demo.py` moves the probe along the outline of a square, stopping at evenly
spaced points on each edge. It is useful for checking the stage and the
coordinate system.

```bash
python xy-demo.py --port /dev/ttyUSB0 --x0 0 --y0 0 --side-mm 40 --steps 10 --z-work 0 --z-safe 5 --feed 600
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

- **`ModuleNotFoundError: numpy` / `h5py` / `serial`**: the virtual environment isn't
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
