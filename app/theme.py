# app/theme.py
from dataclasses import dataclass

@dataclass
class Theme:
    # Colors (all hex strings)
    system: str = "#171215"
    tmp: str = "#000000"

    # Radii / sizes
    dock_radius: int = 24    # dock card radius
    shadow_blur: int = 20
    icon_size: int = 20

    app_margins: int = 8
    app_padding: int = 4
    padding: int = 5

    def as_dict(self):
        return self.__dict__
