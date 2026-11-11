"""Classic OpenCV lane pipeline with the motorcycle roll correction.

Steps (§5b.5): (0) undo the camera roll with the lean angle, (1) lens
undistortion when a calibration exists, (2) colour + gradient thresholding
(HLS), (3) bird's-eye perspective transform, (4) sliding-window lane pixel
search + second-order fit, (5) lateral offset of the camera axis from the
lane centre, in metres. ``departure()`` then applies the threshold and the
indicator suppression.

Every number in ``LdwConfig`` is a camera/mount property or a provisional
tuning value for the offline stage; none is a vehicle signal. Thresholds are
tuned on the road later (§5b.5 development order, step 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

Image = np.ndarray


@dataclass(frozen=True)
class Calibration:
    """Lens calibration from a checkerboard (cv2.calibrateCamera)."""

    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray


@dataclass(frozen=True)
class LdwConfig:
    """Camera geometry and the provisional tuning of the offline pipeline."""

    # Four image points (pixels, x/y) of a ground rectangle: bottom-left,
    # bottom-right, top-right, top-left. Mount-specific; set once per camera pose.
    src_quad: tuple[tuple[float, float], ...]
    # Bird's-eye output size and the x range (pixels) the ground rectangle maps to.
    warp_size: tuple[int, int] = (640, 720)
    warp_margin_px: int = 160
    # Real width of the ground rectangle (m): scales pixels to metres.
    rect_width_m: float = 3.5
    # Thresholds (HLS saturation / lightness 0-255, Sobel-x on L normalised 0-255).
    s_thresh: tuple[int, int] = (120, 255)
    l_thresh: tuple[int, int] = (200, 255)
    sx_thresh: tuple[int, int] = (40, 255)
    # Sliding windows.
    n_windows: int = 9
    window_margin_px: int = 60
    min_pixels: int = 40
    # Minimum lane pixels per side to accept a fit.
    min_lane_pixels: int = 300
    # Departure threshold (m), provisional: looser than a car's (§5b.5).
    departure_threshold_m: float = 0.45
    calibration: Calibration | None = field(default=None, compare=False)

    @property
    def dst_quad(self) -> tuple[tuple[float, float], ...]:
        w, h = self.warp_size
        m = self.warp_margin_px
        return ((m, h), (w - m, h), (w - m, 0), (m, 0))

    @property
    def m_per_px(self) -> float:
        return self.rect_width_m / (self.warp_size[0] - 2 * self.warp_margin_px)


@dataclass(frozen=True)
class LaneFit:
    left: np.ndarray  # polyfit coefficients x = a*y^2 + b*y + c (warped pixels)
    right: np.ndarray
    left_pixels: int
    right_pixels: int


@dataclass(frozen=True)
class LaneResult:
    offset_m: float  # camera axis minus lane centre; positive = right of centre
    lane_width_m: float
    fit: LaneFit


def _roll_matrix(shape: tuple[int, ...], lean_deg: float) -> np.ndarray:
    h, w = shape[:2]
    return cv2.getRotationMatrix2D((w / 2, h / 2), -lean_deg, 1.0)


def derotate(img: Image, lean_deg: float) -> Image:
    """Undo the camera roll: a lean to the right rolls the image clockwise.

    The sign convention follows the DBC ``LEAN_ANGLE`` (positive = right).
    Pixels with no source (the corners the roll uncovers) are filled with the
    edge value; ``roll_valid_mask()`` tells which pixels are real.
    """
    if lean_deg == 0:
        return img
    h, w = img.shape[:2]
    rot = _roll_matrix(img.shape, lean_deg)
    return cv2.warpAffine(img, rot, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def roll_valid_mask(shape: tuple[int, ...], lean_deg: float, erode_px: int = 5) -> Image:
    """1 where ``derotate()`` had a source pixel, eroded so the seam adds no edge."""
    h, w = shape[:2]
    if lean_deg == 0:
        return np.ones((h, w), dtype=np.uint8)
    ones = np.ones((h, w), dtype=np.uint8)
    valid = cv2.warpAffine(
        ones, _roll_matrix(shape, lean_deg), (w, h), flags=cv2.INTER_NEAREST, borderValue=0
    )
    kernel = np.ones((2 * erode_px + 1, 2 * erode_px + 1), dtype=np.uint8)
    return cv2.erode(valid, kernel)


def undistort(img: Image, calibration: Calibration | None) -> Image:
    if calibration is None:
        return img
    return cv2.undistort(img, calibration.camera_matrix, calibration.dist_coeffs)


def threshold(img_bgr: Image, cfg: LdwConfig) -> Image:
    """Binary lane-candidate mask from HLS colour and the x gradient of L."""
    hls = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HLS)
    light = hls[:, :, 1]
    sat = hls[:, :, 2]
    sobel = np.abs(cv2.Sobel(light, cv2.CV_64F, 1, 0, ksize=3))
    peak = sobel.max()
    sobel8 = np.uint8(255 * sobel / peak) if peak > 0 else np.zeros_like(light)
    mask = (
        _in_range(sat, cfg.s_thresh)
        | _in_range(light, cfg.l_thresh)
        | _in_range(sobel8, cfg.sx_thresh)
    )
    return mask.astype(np.uint8)


def _in_range(channel: Image, bounds: tuple[int, int]) -> Image:
    lo, hi = bounds
    return (channel >= lo) & (channel <= hi)


def perspective_matrices(cfg: LdwConfig) -> tuple[np.ndarray, np.ndarray]:
    src = np.float32(cfg.src_quad)
    dst = np.float32(cfg.dst_quad)
    return cv2.getPerspectiveTransform(src, dst), cv2.getPerspectiveTransform(dst, src)


def birds_eye(binary: Image, cfg: LdwConfig) -> Image:
    matrix, _ = perspective_matrices(cfg)
    return cv2.warpPerspective(binary, matrix, cfg.warp_size, flags=cv2.INTER_NEAREST)


def fit_lanes(warped: Image, cfg: LdwConfig) -> LaneFit | None:
    """Histogram bases + sliding windows + second-order polyfit per side."""
    h, w = warped.shape[:2]
    histogram = warped[h // 2 :, :].sum(axis=0)
    mid = w // 2
    left_base = int(np.argmax(histogram[:mid]))
    right_base = mid + int(np.argmax(histogram[mid:]))
    if histogram[left_base] == 0 or histogram[right_base] == 0:
        return None

    nonzero_y, nonzero_x = warped.nonzero()
    window_h = h // cfg.n_windows
    lanes: list[np.ndarray] = []
    counts: list[int] = []
    for base in (left_base, right_base):
        current = base
        picked: list[np.ndarray] = []
        for i in range(cfg.n_windows):
            y_lo = h - (i + 1) * window_h
            y_hi = h - i * window_h
            x_lo = current - cfg.window_margin_px
            x_hi = current + cfg.window_margin_px
            idx = np.where(
                (nonzero_y >= y_lo) & (nonzero_y < y_hi) & (nonzero_x >= x_lo) & (nonzero_x < x_hi)
            )[0]
            picked.append(idx)
            if len(idx) > cfg.min_pixels:
                current = int(nonzero_x[idx].mean())
        idx = np.concatenate(picked)
        counts.append(len(idx))
        if len(idx) < cfg.min_lane_pixels:
            return None
        lanes.append(np.polyfit(nonzero_y[idx], nonzero_x[idx], 2))
    return LaneFit(left=lanes[0], right=lanes[1], left_pixels=counts[0], right_pixels=counts[1])


def lane_offset(fit: LaneFit, cfg: LdwConfig) -> LaneResult:
    """Offset of the camera axis (image centre) from the lane centre at the bottom row."""
    w, h = cfg.warp_size
    y = h - 1
    left_x = float(np.polyval(fit.left, y))
    right_x = float(np.polyval(fit.right, y))
    centre_x = (left_x + right_x) / 2
    offset_m = (w / 2 - centre_x) * cfg.m_per_px
    width_m = (right_x - left_x) * cfg.m_per_px
    return LaneResult(offset_m=offset_m, lane_width_m=width_m, fit=fit)


def process_frame(img_bgr: Image, cfg: LdwConfig, lean_deg: float = 0.0) -> LaneResult | None:
    """Full offline pipeline on one BGR frame; ``None`` when no lane pair is found."""
    frame = undistort(derotate(img_bgr, lean_deg), cfg.calibration)
    mask = threshold(frame, cfg) & roll_valid_mask(img_bgr.shape, lean_deg)
    warped = birds_eye(mask, cfg)
    fit = fit_lanes(warped, cfg)
    return lane_offset(fit, cfg) if fit is not None else None


def departure(result: LaneResult | None, cfg: LdwConfig, indicator_on: bool = False) -> bool:
    """Warn when the lateral offset crosses the threshold and no indicator is on (§5b.5)."""
    if result is None or indicator_on:
        return False
    return abs(result.offset_m) >= cfg.departure_threshold_m
