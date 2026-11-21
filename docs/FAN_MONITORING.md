# Read-only fan monitoring

The dashboard's Fans section displays exposed Linux hwmon RPM readings alongside
GPU telemetry. It uses the existing authenticated `/api/system` response and
shared SSE sampler. No new route, credential, background service, dependency,
driver installation or Compose permission is added. Production remains entirely
on Ubuntu; Windows mock mode supplies clearly labelled simulated readings.

The collector only opens controller `name` and matching `fanN_input` attributes
for reading. It never opens PWM, mode, minimum-RPM, alarm or other control
attributes; never executes commands; and never loads drivers or changes host
configuration. The hardware driver's own initialization is outside this
application's read-only boundary. Do not load a driver merely to populate this UI.

Discovery uses `/sys/class/hwmon` visible to the API process. Standard controller
symlinks are supported. Enumeration and text reads are bounded, with at most 32
controllers, 16 inputs per controller and 64 total readings per sample. Partial
or truncated results are labelled. Device identity derives from the controller's
device path basename rather than relying solely on volatile hwmon numbers.

When the driver is absent, sysfs is hidden, or readings are unreadable, the panel
reports unavailable. It does not retain a disappeared channel as fresh data.
Missing or invalid RPM is null, distinct from a measured zero. Zero means no
rotation was reported; it can also indicate an unused input or missing tachometer
signal, so the UI does not declare a fan failure. Sensor-channel labels do not
claim verified motherboard headers or GPU assignments.

Zero-RPM inputs are hidden by default to reduce unused-header clutter. A
“Show zero-RPM inputs” toggle reveals them without changing sensor collection or
hardware settings. Filtering uses each latest reading, so a newly connected fan
appears automatically once it reports positive RPM, and an input reporting zero
is hidden again unless the toggle is on. Unreadable inputs remain visible as
unavailable. The display preference lasts for the mounted panel only; it does not
change API data, hide inputs permanently or persist browser credentials.

Fan data inherits the existing stream cadence and stale warnings. Some monitoring
chips refresh more slowly than the UI; a new stream frame is not proof of a new
hardware conversion. A stale sample explicitly says current speeds are unknown.
Older API versions without a fans field remain supported by the frontend.

## Current P100 server boundary

On the operator's X99-SLI-CF, an earlier external mapping experiment was followed
by sudden power-off during an IT8620 PWM4 write. Those scripts are retired. This
feature does not contain or call them. The controller driver currently remains
unloaded, so the production panel is expected to report unavailable until a
separately reviewed hardware-monitoring solution exposes RPMs.

The operator identified original P100 cooling on SYS_FAN3 and newer P100 cooling
on SYS_FAN2. Their Linux sensor-channel assignments are not established and are
not hardcoded into the UI. No direct register probing, forced chip IDs, firmware
resource overrides, automatic fan calibration or driver loading is authorized by
enabling this panel. The SpeedFan developer's historical warning for IT8620E PWM4
on some Gigabyte boards reinforces that monitoring support is not permission to
write arbitrary exposed control channels:
https://almico.com/sfarticle.php?id=6

## Validation

Backend tests cover read-only attribute access, malformed/denied/missing data,
sensor removal, bounds, live sample integration and mock isolation. Browser tests
cover numeric/zero/unavailable readings, stale and disappeared data, stream
updates, absence of controls, mock labelling and mobile layout. Container
acceptance checks fan fields in both authenticated REST and real HTTP SSE.
Synthetic files and mocks are software evidence, not new physical fan readings.
Merge and production deployment remain separate operator actions.
