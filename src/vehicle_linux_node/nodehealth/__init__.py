"""L2 node-health panel (D-108, provisional): one row per platform-bus node.

Reads the ``Vehicle.Motorcycle.Nodes.<Node>.{Mode,ErrorCount,UptimeSeconds}`` leaves
from Kuksa. Node names, value names and the stale rule all come from the pinned defs.
Display only: nothing here feeds a decision (D-108). Pure logic in ``core``, the Kuksa
subscription in ``service``.
"""
