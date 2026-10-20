FURNACE DASHBOARD - Raspberry Pi Pico + HTML

RUN
1. Install Python 3.10 or newer on Windows and enable "Add Python to PATH".
2. Connect Pico by USB. Close Thonny's running serial session and close Serial Studio.
   Only one application can normally read the same COM port at a time.
3. Double-click run_windows.bat.
4. Open http://127.0.0.1:8765 in your browser.

SERIAL PORT
The app tries to detect the Pico COM port. If it chooses incorrectly, edit main.py:
    SERIAL_PORT = "COM3"
Replace COM3 with the port shown in Windows Device Manager > Ports (COM & LPT).

PICO MICROPYTHON SENDER
The dashboard expects exactly 12 comma-separated values per line:
T1,T2,T3,T4,ElapsedSeconds,Seconds,Minutes,Hours,TotalMinutes,TotalHours,Days,SampleCounter

Example simulated sender:
    import time, math, random
    start_time = time.ticks_ms()
    base_temperatures = [650.0, 720.0, 580.0, 810.0]
    sample_counter = 0
    while True:
        elapsed_ms = time.ticks_diff(time.ticks_ms(), start_time)
        elapsed_seconds = elapsed_ms // 1000
        seconds = elapsed_seconds % 60
        minutes = (elapsed_seconds // 60) % 60
        hours = (elapsed_seconds // 3600) % 24
        total_minutes = elapsed_seconds // 60
        total_hours = elapsed_seconds // 3600
        days = elapsed_seconds // 86400
        elapsed = elapsed_ms / 1000
        temperatures = []
        for i, base in enumerate(base_temperatures):
            variation = 8 * math.sin(elapsed / 15 + i)
            noise = random.uniform(-1.5, 1.5)
            temperatures.append(base + variation + noise)
        sample_counter += 1
        values = [round(t, 1) for t in temperatures]
        values.extend([elapsed_seconds, seconds, minutes, hours, total_minutes,
                       total_hours, days, sample_counter])
        print(",".join(str(v) for v in values))
        time.sleep(1)

The Pico timer resets when the Pico program restarts. The included dashboard is a prototype,
not a safety-rated furnace controller. Later, replace simulated temperatures with MAX31855 reads.
