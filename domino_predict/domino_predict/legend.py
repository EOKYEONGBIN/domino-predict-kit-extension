"""
Color legend for the prediction Faces, drawn as an overlay in the bottom-right
of the active viewport so it shows up next to the model (and in screenshots).
One shared color bar with a row of tick values per field underneath. Uses
the same colormap image Kit-CAE's Faces material samples (omni.cae.viz's
cae/colormaps/gist_rainbow.png), so the bar matches the surface colors exactly.
"""

import asyncio
import os

import omni.kit.app
import omni.ui as ui

_FRAME_NAME = "domino_predict_legend"
_N_TICKS = 5
_BAR_WIDTH = 300
_BAR_HEIGHT = 14
_NAME_WIDTH = 260
_PAD = 10


def _colormap_path() -> str | None:
    manager = omni.kit.app.get_app().get_extension_manager()
    ext_id = manager.get_enabled_extension_id("omni.cae.viz")
    if not ext_id:
        return None
    path = os.path.join(manager.get_extension_path(ext_id), "material_library", "cae", "colormaps", "gist_rainbow.png")
    return path if os.path.isfile(path) else None


def _format_tick(value: float, vmin: float, vmax: float) -> str:
    span = abs(vmax - vmin)
    if span < 1e-3:  # e.g. nutMean, ~1.5e-4
        return "0" if value == 0 else f"{value:.1e}"
    # Small ranges (e.g. wall shear stress, ~0.007) need more decimals.
    return f"{value:.4f}" if span < 0.1 else f"{value:.2f}"


class ColorLegend:
    def __init__(self, fields: list[tuple[str, float, float]]):
        """fields: (title, vmin, vmax) per tick row, top to bottom; (title, None, None) is a section title."""
        self._fields = fields
        self._frame = None
        self._wanted = False

    def request_show(self) -> None:
        """Marks the legend as wanted right away (so a hide() that comes
        later always wins over a show still waiting), then shows it once the
        viewport is ready."""
        self._wanted = True
        asyncio.ensure_future(self._show_when_ready())

    async def _show_when_ready(self, max_frames: int = 600) -> None:
        """The viewport may not exist yet when the extension starts up, so
        keep trying for a while instead of giving up on the first frame."""
        app = omni.kit.app.get_app()
        for _ in range(max_frames):
            if not self._wanted:  # turned off again while waiting
                return
            if self.show():
                return
            await app.next_update_async()

    def set_fields(self, fields: list[tuple[str, float, float]]) -> None:
        self._fields = fields

    def show(self) -> bool:
        """Builds the overlay (repeated calls just rebuild it). Returns False
        if there's no viewport to draw into yet."""
        from omni.kit.viewport.utility import get_active_viewport_window

        viewport_window = get_active_viewport_window()
        if viewport_window is None:
            return False
        self._frame = viewport_window.get_frame(_FRAME_NAME)
        self._frame.clear()  # rebuild with the current rows
        self._frame.visible = True
        image_path = _colormap_path()
        text_style = {"color": 0xFFFFFFFF, "font_size": 14}
        section_style = {"color": 0xFFB0B0B0, "font_size": 14}

        # One shared color bar (every field uses the same colormap), then one
        # row per field: name on the left, its tick values under the bar.
        with self._frame:
            with ui.VStack():
                ui.Spacer()
                with ui.HStack(height=0):
                    ui.Spacer()
                    with ui.ZStack(width=_PAD + _NAME_WIDTH + _BAR_WIDTH + _PAD, height=0):
                        ui.Rectangle(style={"background_color": 0xA0000000, "border_radius": 4})
                        with ui.VStack(height=0, spacing=6):
                            ui.Spacer(height=2)
                            with ui.HStack(height=_BAR_HEIGHT):
                                ui.Spacer(width=_PAD + _NAME_WIDTH)
                                if image_path:
                                    ui.Image(
                                        image_path,
                                        width=_BAR_WIDTH,
                                        height=_BAR_HEIGHT,
                                        fill_policy=ui.FillPolicy.STRETCH,
                                    )
                                else:
                                    ui.Label("(colormap image not found)", style=text_style)
                            for title, vmin, vmax in self._fields:
                                if vmin is None:  # section title
                                    with ui.HStack(height=0):
                                        ui.Spacer(width=_PAD)
                                        ui.Label(title, style=section_style)
                                    continue
                                with ui.HStack(height=0):
                                    ui.Spacer(width=_PAD)
                                    ui.Label(title, width=_NAME_WIDTH, style=text_style)
                                    with ui.HStack(width=_BAR_WIDTH):
                                        for i in range(_N_TICKS):
                                            value = vmin + (vmax - vmin) * i / (_N_TICKS - 1)
                                            if i:
                                                ui.Spacer()
                                            ui.Label(_format_tick(value, vmin, vmax), width=0, style=text_style)
                            ui.Spacer(height=2)
                    ui.Spacer(width=16)
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
