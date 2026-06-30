from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SizeBin:
    low_um: float
    high_um: float
    code: str
    label: str
    color_bgr: tuple[int, int, int]
    color_hex: str
    include_low: bool = False
    include_high: bool = True

    def contains(self, length_um: float) -> bool:
        above_low = length_um >= self.low_um if self.include_low else length_um > self.low_um
        below_high = length_um <= self.high_um if self.include_high else length_um < self.high_um
        return above_low and below_high


@dataclass(frozen=True)
class AnalysisMode:
    key: str
    name: str
    minimum_size_um: float
    bins: tuple[SizeBin, ...]
    required_pixels: int | None = None

    def classify(self, length_um: float) -> SizeBin:
        for size_bin in self.bins:
            if size_bin.contains(length_um):
                return size_bin
        raise ValueError(f"颗粒尺寸 {length_um:.2f} μm 不在 {self.name} 的任何分档范围内。")

    def should_report(self, length_um: float, configured_minimum_um: float | None = None) -> bool:
        minimum = max(
            self.minimum_size_um,
            configured_minimum_um if configured_minimum_um is not None else self.minimum_size_um,
        )
        if length_um != minimum:
            return length_um > minimum
        first_bin = self.bins[0]
        return minimum == first_bin.low_um and first_bin.include_low

    def validate_resolution(self, um_per_px: float) -> dict:
        minimum_particle_pixels = self.minimum_size_um / um_per_px
        compliant = self.required_pixels is None or minimum_particle_pixels >= self.required_pixels
        result = {
            "minimum_size_um": self.minimum_size_um,
            "minimum_particle_pixels": round(minimum_particle_pixels, 2),
            "required_pixels": self.required_pixels,
            "compliant": compliant,
        }
        if not compliant:
            raise ValueError(
                f"{self.name}要求 {self.minimum_size_um:g} μm 颗粒至少需要 "
                f"{self.required_pixels} 像素；当前仅 {minimum_particle_pixels:.2f} 像素。"
            )
        return result


CUSTOM_MODE = AnalysisMode(
    key="custom",
    name="自定义模式",
    minimum_size_um=25.0,
    bins=(
        SizeBin(25.0, 50.0, "", "25<n<=50", (35, 181, 106), "#36a673"),
        SizeBin(50.0, 100.0, "", "50<n<=100", (25, 158, 234), "#f0ad35"),
        SizeBin(100.0, 200.0, "", "100<n<=200", (42, 54, 222), "#d93e47"),
        SizeBin(200.0, math.inf, "", "n>200", (180, 51, 170), "#aa33b4"),
    ),
)

VDA_19_1_MODE = AnalysisMode(
    key="vda19_1",
    name="VDA 19.1 模式",
    minimum_size_um=50.0,
    bins=(
        SizeBin(50.0, 100.0, "E", "E (50<=n<100)", (90, 133, 47), "#2f855a", True, False),
        SizeBin(100.0, 150.0, "F", "F (100<=n<150)", (108, 149, 76), "#4c956c", True, False),
        SizeBin(150.0, 200.0, "G", "G (150<=n<200)", (139, 154, 108), "#6c9a8b", True, False),
        SizeBin(200.0, 400.0, "H", "H (200<=n<400)", (23, 160, 212), "#d4a017", True, False),
        SizeBin(400.0, 600.0, "I", "I (400<=n<600)", (34, 122, 221), "#dd7a22", True, False),
        SizeBin(600.0, 1000.0, "J", "J (600<=n<1000)", (57, 93, 217), "#d95d39", True, False),
        SizeBin(1000.0, 1500.0, "K", "K (1000<=n<1500)", (74, 59, 194), "#c23b4a", True, False),
        SizeBin(1500.0, 2000.0, "L", "L (1500<=n<2000)", (150, 77, 155), "#9b4d96", True, False),
        SizeBin(2000.0, 3000.0, "M", "M (2000<=n<3000)", (193, 66, 111), "#6f42c1", True, False),
        SizeBin(3000.0, math.inf, "N", "N (n>=3000)", (139, 52, 61), "#3d348b", True, True),
    ),
    required_pixels=10,
)


def get_analysis_mode(key: str) -> AnalysisMode:
    if key == CUSTOM_MODE.key:
        return CUSTOM_MODE
    if key == VDA_19_1_MODE.key:
        return VDA_19_1_MODE
    raise ValueError(f"未知分析模式：{key}")
