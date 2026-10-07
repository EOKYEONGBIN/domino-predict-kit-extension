"""
Color legend for the prediction Faces, drawn as an overlay in the bottom-left
of the active viewport so it shows up next to the model (and in screenshots).
One color bar per field, stacked. Uses the same colormap image Kit-CAE's
Faces material samples (omni.cae.viz's cae/colormaps/gist_rainbow.png), so
the bars match the surface colors exactly.
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


def _format_tick(value: float, vmin: float, vmax: float) -> str:
    # Small ranges (e.g. wall shear stress, ~0.007) need more decimals.
    return f"{value:.4f}" if abs(vmax - vmin) < 0.1 else f"{value:.2f}"


class ColorLegend:
    def __init__(self, fields: list[tuple[str, float, float]]):
        """fields: (title, vmin, vmax) per color bar, top to bottom."""
        self._fields = fields
        self._frame = None
        self._wanted = False

    async def show_when_ready(self, max_frames: int = 600) -> None:
        """The viewport may not exist yet when the extension starts up, so
        keep trying for a while instead of giving up on the first frame."""
        self._wanted = True
        app = omni.kit.app.get_app()
        for _ in range(max_frames):
            if not self._wanted:  # turned off again while waiting
                return
            if self.show():
                return
            await app.next_update_async()

    def show(self) -> bool:
        """Builds the overlay (repeated calls just rebuild it). Returns False
        if there's no viewport to draw into yet."""
        from omni.kit.viewport.utility import get_active_viewport_window

        viewport_window = get_active_viewport_window()
        if viewport_window is None:
            return False
        self._frame = viewport_window.get_frame(_FRAME_NAME)
        self._frame.visible = True
        image_path = _colormap_path()
        text_style = {"color": 0xFFFFFFFF, "font_size": 14}

        with self._frame:
            with ui.VStack():
                ui.Spacer()
                with ui.HStack(height=0):
                    ui.Spacer(width=16)
                    with ui.ZStack(width=_BAR_WIDTH + 16, height=0):
                        ui.Rectangle(style={"background_color": 0xA0000000, "border_radius": 4})
                        with ui.VStack(height=0, spacing=4):
                            for title, vmin, vmax in self._fields:
                                ui.Spacer(height=4)
                                with ui.HStack(height=0):
                                    ui.Spacer(width=8)
                                    ui.Label(title, style=text_style, height=0)
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
                                    for i in range(_N_TICKS):
                                        value = vmin + (vmax - vmin) * i / (_N_TICKS - 1)
                                        if i:
                                            ui.Spacer()
                                        ui.Label(_format_tick(value, vmin, vmax), width=0, style=text_style)
                                    ui.Spacer(width=8)
                            ui.Spacer(height=4)
                    ui.Spacer()
                ui.Spacer(height=16)
        return True

    def hide(self) -> None:
        self._wanted = False
        if self._frame is not None:
            self._frame.clear()
            self._frame.visible = False
            self._frame = None

    def destroy(self) -> None:
        self.hide()
