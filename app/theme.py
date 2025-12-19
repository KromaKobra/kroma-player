# app/theme.py
from dataclasses import dataclass

@dataclass
class Theme:
    # Colors (all hex strings)
    bg_color: str = "#0f1720"
    board_color: str = "#0f1720"
    dock_color: str = "#0b1220"
    accent: str = "#1f6feb"
    text: str = "#e6eef8"
    subtle: str = "#99a3b3"

    # Radii / sizes
    border_radius: int = 18   # outer board radius
    inner_radius: int = 12    # dock card radius
    shadow_blur: int = 20
    icon_size: int = 20

    def as_dict(self):
        return self.__dict__
