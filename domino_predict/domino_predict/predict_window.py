import omni.ui as ui

WINDOW_TITLE = "DoMINO Prediction"
SETTINGS_FRAME_TITLE = "Remote Server Settings"

# omni.ui colors are 0xAABBGGRR. Material green 500 / red 500 / grey 500.
_COLOR_CONNECTED = 0xFF50AF4C
_COLOR_DISCONNECTED = 0xFF3539E5
_COLOR_CHECKING = 0xFF9E9E9E


class PredictWindow(ui.Window):
    def __init__(self, request_clicked_fn, browse_clicked_fn, connect_clicked_fn, initial_host: str = "", **kwargs):
        super().__init__(WINDOW_TITLE, width=420, height=340, **kwargs)
        self._request_clicked_fn = request_clicked_fn
        self._browse_clicked_fn = browse_clicked_fn
        self._connect_clicked_fn = connect_clicked_fn
        # AUDIT FIX (2026-09-29): set_build_fn's callback doesn't necessarily
        # run synchronously inside __init__ (it can be deferred to the next
        # frame) -- extension.py used to set `.host` right after
        # construction, which hit `_host_field` before _build() had created
        # it (AttributeError). Threading the initial value through the
        # constructor instead means _build() itself, whenever it actually
        # runs, has it available to seed the field with.
        self._initial_host = initial_host
        self.frame.set_build_fn(self._build)

    def _build(self):
        with self.frame:
            with ui.VStack(spacing=6, height=0):
                # AUDIT FIX (2026-09-29): remote host used to be a hard-coded
                # constant in remote_predict.py -- moved to the top of the
                # window (was a "Settings" button that toggled a frame lower
                # down) since it's the first thing to check/set up on a
                # fresh machine, before there's even an STL to pick. Still
                # collapsed by default so the common case (host already set,
                # connection already verified) doesn't add visual clutter to
                # every request -- click the header row to expand it.
                #
                # AUDIT FIX (2026-09-29): a real ui.CollapsableFrame was used
                # here first, with the status folded into its .title string
                # and .style reassigned on every status change. In the real
                # Kit-CAE runtime the title text never actually picked up
                # the color -- CollapsableFrame's header doesn't reliably
                # re-style after construction. Replaced with a manually
                # built header: a toggle-arrow button plus two separate
                # Labels, so "Connect"/"Disconnect" is its own ui.Label with
                # its own `style={"color": ...}`, which does reliably apply.
                self._settings_expanded = False
                with ui.HStack(height=24):
                    # AUDIT FIX (2026-10-01): the Unicode triangles (U+25B6/
                    # U+25BC) rendered as "?" (missing-glyph box) in Kit-CAE's
                    # UI font, which doesn't cover that block. Plain ASCII
                    # renders everywhere.
                    self._settings_toggle_button = ui.Button(
                        ">", width=20, height=20,
                        clicked_fn=self._toggle_settings,
                        style={"Button": {"background_color": 0x0, "border_width": 0}},
                    )
                    ui.Label(f"{SETTINGS_FRAME_TITLE} - ", width=0)
                    self._settings_status_label = ui.Label("Checking...", style={"color": _COLOR_CHECKING})

                self._settings_content_frame = ui.Frame(height=0, visible=False)
                with self._settings_content_frame:
                    with ui.VStack(spacing=6, height=0):
                        ui.Label("Server:")
                        with ui.HStack(height=24):
                            self._host_field = ui.StringField()
                            self._host_field.model.set_value(self._initial_host)
                            ui.Button("Connect", width=70, clicked_fn=self._connect_clicked_fn)

                ui.Spacer(height=8)
                ui.Label("Input STL:")
                with ui.HStack(height=24):
                    self._stl_path_field = ui.StringField()
                    ui.Button("Browse...", width=70, clicked_fn=self._browse_clicked_fn)

                ui.Spacer(height=8)
                ui.Label("Visualize:")
                with ui.HStack(height=24):
                    self._faces_checkbox = ui.CheckBox(width=20)
                    self._faces_checkbox.model.set_value(True)
                    ui.Label("Faces (surface pressure / wall shear)")
                with ui.HStack(height=24):
                    self._streamlines_checkbox = ui.CheckBox(width=20)
                    self._streamlines_checkbox.model.set_value(True)
                    ui.Label("Streamlines (volume velocity)")

                ui.Spacer(height=8)
                self._request_button = ui.Button(
                    "Request Prediction", height=32, clicked_fn=self._request_clicked_fn
                )

                ui.Spacer(height=8)
                self._status_label = ui.Label("Idle.", word_wrap=True)

    @property
    def stl_path(self) -> str:
        return self._stl_path_field.model.get_value_as_string()

    @stl_path.setter
    def stl_path(self, value: str) -> None:
        self._stl_path_field.model.set_value(value)

    @property
    def host(self) -> str:
        return self._host_field.model.get_value_as_string()

    @host.setter
    def host(self, value: str) -> None:
        self._host_field.model.set_value(value)

    @property
    def compute_faces(self) -> bool:
        return self._faces_checkbox.model.get_value_as_bool()

    @property
    def compute_streamlines(self) -> bool:
        return self._streamlines_checkbox.model.get_value_as_bool()

    def _toggle_settings(self) -> None:
        self._settings_expanded = not self._settings_expanded
        self._settings_content_frame.visible = self._settings_expanded
        self._settings_toggle_button.text = "v" if self._settings_expanded else ">"

    def set_connection_status(self, connected: bool | None) -> None:
        """connected=True -> "Connect" (green), False -> "Disconnect"
        (red), None -> "Checking..." (grey, while a check is in flight).
        Only the status Label's own style is touched, so the toggle
        button/expanded state next to it is untouched."""
        if connected is None:
            text, color = "Checking...", _COLOR_CHECKING
        elif connected:
            text, color = "Connect", _COLOR_CONNECTED
        else:
            text, color = "Disconnect", _COLOR_DISCONNECTED
        self._settings_status_label.text = text
        self._settings_status_label.style = {"color": color}

    def set_status(self, text: str) -> None:
        self._status_label.text = text

    def set_busy(self, busy: bool) -> None:
        self._request_button.enabled = not busy
