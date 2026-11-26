"""Shared experiment settings.

topology.py, local_test.py and build_slides.py all read these values, so the
slides, the emulated network and the test harness always describe the same
experiment. Change a value here, not in the individual scripts.
"""

# --- Addresses -------------------------------------------------------------
COLLECTOR_IP = "10.0.0.100"      # h1, runs collector.py
COLLECTOR_PORT = 5005            # UDP port of the collector

# name -> (IP address, sensor id, y position of its lane in metres)
STATIONS = {
    "sta1": ("10.0.0.1", 1, 45),
    "sta2": ("10.0.0.2", 2, 55),
}

# --- Access points -----------------------------------------------------------
# name -> (x, y, WiFi channel). The two APs are 50 m apart.
ACCESS_POINTS = {
    "ap1": (30, 50, 1),
    "ap2": (80, 50, 6),
}
AP_RANGE = 25                    # metres. Coverage circles just miss each other
                                 # on the sensor lanes, so every sensor has to
                                 # re-associate (handover) around x = 55 m.
PROPAGATION_MODEL = "logDistance"
PROPAGATION_EXP = 3.5

# --- Mobility ----------------------------------------------------------------
STATIONARY_X = 20                # x position of the sensors when they do not move
PATH_START_X = 10                # mobile sensors travel from x = 10 m ...
PATH_END_X = 100                 # ... to x = 100 m
MOVE_START = 1                   # seconds after the network starts
MOVE_TIME = 40                   # seconds to cover the path (2.25 m/s)

# --- Wired links (APs -> switch -> collector) --------------------------------
LINK_BW = 10                     # Mbit/s
LINK_DELAY = "5ms"               # on the collector link (h1 - s1)
LINK_LOSS = 0                    # percent
QUEUE_SIZE = 100                 # packets (max_queue_size)

# --- Workload ----------------------------------------------------------------
RATE = 10                        # messages per second per sensor
DURATION = 40                    # seconds per benchmark run
RUNS = 3                         # repetitions per case

# --- QoS 1 reliability -------------------------------------------------------
ACK_TIMEOUT = 0.2                # seconds to wait for an ACK
MAX_RETRIES = 3                  # resends after the first transmission
