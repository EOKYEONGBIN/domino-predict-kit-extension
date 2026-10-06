"""
Color legend for the prediction Faces, drawn as an overlay in the bottom-left
of the active viewport so it shows up next to the model (and in screenshots).
Uses the same colormap image Kit-CAE's Faces material samples
(omni.cae.viz's cae/colormaps/gist_rainbow.png), so the bar matches the
surface colors exactly.
"""

import os

import omni.kit.app
import omni.ui as ui

_FRAME_NAME = "domino_predict_legend"
_N_TICKS = 5
_BAR_WIDTH = 260
_BAR_HEIGHT = 14


def _colormap_path() -> str | None:
    manager = omni.kit.app.get_app().get_extension_manager()
    ext_id = manager.get_enabled_extension_id("omni.cae.viz")
    if not ext_id:
        return None
    path = os.path.join(manager.get_extension_path(ext_id), "material_library", "cae", "colormaps", "gist_rainbow.png")
    return path if os.path.isfile(path) else None


class ColorLegend:
    def __init__(self, title: str, vmin: float, vmax: float):
        self._title = title
        self._vmin = vmin
        self._vmax = vmax
        self._frame = None

    def show(self) -> None:
        """Builds the overlay (idempotent -- repeated calls just rebuild it)."""
        from omni.kit.viewport.utility import get_active_viewport_window

        viewport_window = get_active_viewport_window()
        if viewport_window is None:
            return
        self._frame = viewport_window.get_frame(_FRAME_NAME)
        self._frame.visible = True
        image_path = _colormap_path()
        ticks = [self._vmin + (self._vmax - self._vmin) * i / (_N_TICKS - 1) for i in range(_N_TICKS)]
        text_style = {"color": 0xFFFFFFFF, "font_size": 14}

        with self._frame:
            with ui.VStack():
                ui.Spacer()
                with ui.HStack(height=0):
                    ui.Spacer(width=16)
                    with ui.ZStack(width=_BAR_WIDTH + 16, height=0):
                        ui.Rectangle(style={"background_color": 0xA0000000, "border_radius": 4})
                        with ui.VStack(height=0, spacing=4):
                            ui.Spacer(height=4)
                            with ui.HStack(height=0):
                                ui.Spacer(width=8)
                                ui.Label(self._title, style=text_style, height=0)
                            with ui.HStack(height=_BAR_HEIGHT):
                                ui.Spacer(width=8)
                                if image_path:
                                    ui.Image(
                                        image_path,
                                        width=_BAR_WIDTH,
                                        height=_BAR_HEIGHT,
                                        fill_policy=ui.FillPolicy.STRETCH,
                                    )
                                else:
                                    ui.Label("(colormap image not found)", style=text_style)
                            with ui.HStack(height=0):
                                ui.Spacer(width=8)
                                for i, value in enumerate(ticks):
                                    if i:
                                        ui.Spacer()
                                    ui.Label(f"{value:.2f}", width=0, style=text_style)
                                ui.Spacer(width=8)
                            ui.Spacer(height=4)
                    ui.Spacer()
                ui.Spacer(height=16)

    def destroy(self) -> None:
        if self._frame is not None:
            self._frame.clear()
            self._frame.visible = False
            self._frame = None
