"""Synthetic road frames with a known lateral offset, for tests and demos.

A lane is drawn in the bird's-eye plane (two white lines, known lane width
and camera offset), then mapped into the camera image with the inverse of
the pipeline's perspective transform, so the ground truth is exact and no
recorded video is needed (D-033: no ride data in the repo).
"""

from __future__ import annotations

import cv2
import numpy as np

from vehicle_linux_node.ldw.pipeline import Image, LdwConfig, derotate, perspective_matrices

ROAD_BGR = (70, 70, 70)
LINE_BGR = (245, 245, 245)
SKY_BGR = (180, 150, 110)


def default_config(width: int = 1280, height: int = 720) -> LdwConfig:
    """A forward camera: the ground rectangle is a trapezoid in the lower image."""
    return LdwConfig(
        src_quad=(
            (width * 0.15, height * 0.95),
            (width * 0.85, height * 0.95),
            (width * 0.58, height * 0.62),
            (width * 0.42, height * 0.62),
        )
    )


def render_frame(
    cfg: LdwConfig,
    offset_m: float,
    *,
    lane_width_m: float = 3.5,
    lean_deg: float = 0.0,
    image_size: tuple[int, int] = (1280, 720),
    line_width_m: float = 0.12,
    curvature_px: float = 0.0,
) -> Image:
    """Camera image of a lane whose centre is ``offset_m`` left of the camera axis.

    ``offset_m`` follows the pipeline sign: positive = the camera (bike) sits
    right of the lane centre. ``lean_deg`` rolls the image as a right lean does.
    """
    w, h = cfg.warp_size
    bird = np.full((h, w, 3), ROAD_BGR, dtype=np.uint8)
    px = 1 / cfg.m_per_px
    centre_x = w / 2 - offset_m * px
    half = lane_width_m / 2 * px
    thickness = max(2, int(round(line_width_m * px)))
    ys = np.arange(h)
    for sign in (-1, 1):
        xs = centre_x + sign * half + curvature_px * ((h - ys) / h) ** 2
        pts = np.stack([xs, ys], axis=1).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(bird, [pts], False, LINE_BGR, thickness, lineType=cv2.LINE_AA)

    _, inverse = perspective_matrices(cfg)
    width, height = image_size
    frame = np.full((height, width, 3), SKY_BGR, dtype=np.uint8)
    horizon = int(min(p[1] for p in cfg.src_quad))
    frame[horizon:, :] = ROAD_BGR
    camera = cv2.warpPerspective(
        bird,
        inverse,
        image_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_TRANSPARENT,
        dst=frame,
    )
    # A right lean rolls the camera clockwise, i.e. the scene appears rotated
    # counter-clockwise; derotate(-lean) applies exactly that.
    return derotate(camera, -lean_deg)
