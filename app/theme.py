# app/theme.py
from dataclasses import dataclass

@dataclass
class Theme:
    # Colors (all hex strings)
    system: str = "#171215"
    system2: str = "#2e282c"
    secondary: str = "#9d6794"
    tmp: str = "#000000"

    # Radii / sizes
    dock_radius: int = 24    # dock card radius
    shadow_blur: int = 20
    icon_size: int = 20

    # Dock Settings
    playlist_margins: int = 20

    # App Settings
    app_margins: int = 14
    app_padding: int = 6
    padding: int = 3
    menubar_height: int = 35

    def as_dict(self):
        return self.__dict__
