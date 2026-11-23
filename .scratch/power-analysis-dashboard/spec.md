# Power analysis dashboard

Status: Completed

Audience: Engineers investigating the ESP32 INA239 power traces of GPS and LTE operations.
Job: Correlate a selected interval with electrical behavior and persist named events.

Flow: Select time -> inspect trace -> select interval -> quantify -> label -> export.

Acceptance functions:
- Preset/custom time, pan, zoom in/out, zoom selection, live, pause/manual refresh.
- Shared hover cursor with displayed values and timestamps; no values invented inside gaps; keyboard arrows inspect points.
- Metric, unit, window averages, channels, optional zero baseline.
- Raw sample statistics: count, min/max/sample mean/latest, valid integration duration, charge mAh, energy mWh.
- Trapezoidal integration uses raw points, skips invalid points and gaps above 3 seconds, and never extrapolates.
- Selection controls, labels persisted in InfluxDB, event list with select/jump, aligned event ribbon.
- Raw measurements and labels CSV for the visible or selected interval.
- Responsive layout, keyboard focus, empty/loading/error guidance, preserve monitor and deletion behavior.

Design tokens: pearl #f3f3f8; white #ffffff; graphite #29243e; cobalt #5260c9; teal #237f81; brass #ae792d.
Type: Optima/Trebuchet MS display; Segoe UI/Helvetica Neue body; Menlo/Consolas utility data.
Signature: An operation ribbon aligned with the trace's time axis connects GPS/LTE events to waveform inspection.

```text
Power trace                              acquisition status
Analysis / Device                       port / latest / count
Time: preset + previous/next + live + From/To + refresh
+--------------------------------------+-------------------+
| metric / unit / average / channels    | Interval notebook |
|                                      | selected duration |
| waveform + cursor tooltip            | raw statistics    |
| aligned operation ribbon             | charge / energy   |
| zoom / selection / export            | label input/save  |
+--------------------------------------+-------------------+
| Saved intervals: name / duration / jump                  |
+---------------------------------------------------------+
Device tab: current readings + raw serial + database management
```

No simulated data is shown in production. Preview fixtures are illustrative.
No new libraries, label editing, or multi-dashboard management. Label deletion
uses a persistent append-only InfluxDB deletion-marker table.
