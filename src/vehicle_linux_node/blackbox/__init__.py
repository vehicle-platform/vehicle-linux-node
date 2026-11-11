"""Event-based black-box recorder (hardware-architecture §5b.8).

Keeps the last few seconds of every subscribed VSS signal in memory and, on
a trigger, writes the window before and after it to separate storage. Pure
logic in ``core``, files in ``store``, the Kuksa subscription in ``service``.
Position signals are never recorded (D-076, invariant 7).
"""
