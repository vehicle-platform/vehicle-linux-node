"""Lane departure warning, classic computer vision (hardware-architecture §5b.5).

Offline stage: the pipeline runs on still images and recorded video; nothing
here opens a camera, CAN or Kuksa. Lean angle and the indicator state are
plain inputs so a caller can feed them from VSS later
(``Vehicle.Motorcycle.LeanAngle``) without changing the maths.
"""
