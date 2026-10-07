import omni.ui as ui

WINDOW_TITLE = "DoMINO Prediction"

MODE_LOCAL = "local"
MODE_REMOTE = "remote"

# omni.ui colors are 0xAABBGGRR. Material green 500 / red 500 / grey 500.
_COLOR_CONNECTED = 0xFF50AF4C
_COLOR_DISCONNECTED = 0xFF3539E5
_COLOR_CHECKING = 0xFF9E9E9E


class PredictWindow(ui.Window):
    def __init__(
        self,
        request_clicked_fn,
        browse_clicked_fn,
        connect_clicked_fn,
        mode_changed_fn,
        install_clicked_fn,
        legend_toggled_fn,
        save_clicked_fn,
        initial_mode: str = MODE_REMOTE,
        initial_host: str = "",
        **kwargs,
    ):
        super().__init__(WINDOW_TITLE, width=420, height=450, **kwargs)
        self._request_clicked_fn = request_clicked_fn
        self._browse_clicked_fn = browse_clicked_fn
        self._connect_clicked_fn = connect_clicked_fn
        self._mode_changed_fn = mode_changed_fn
        self._install_clicked_fn = install_clicked_fn
        self._legend_toggled_fn = legend_toggled_fn
        self._save_clicked_fn = save_clicked_fn
        # AUDIT FIX (2026-09-29): set_build_fn's callback doesn't necessarily
        # run synchronously inside __init__ (it can be deferred to the next
        # frame) -- extension.py used to set `.host` right after
        # construction, which hit `_host_field` before _build() had created
        # it (AttributeError). Threading the initial values through the
        # constructor instead means _build() itself, whenever it actually
        # runs, has them available to seed the widgets with.
        self._mode = initial_mode
        self._initial_host = initial_host
        self._updating_mode = False
        self.frame.set_build_fn(self._build)

    def _build(self):
        with self.frame:
            with ui.VStack(spacing=6, height=0):
                # Header: "Server Settings" button toggles the panel below; the
                # status sits next to it as its own Label because
                # CollapsableFrame's header doesn't reliably re-style after
                # construction (the status color never showed in Kit-CAE).
                with ui.HStack(height=24):
                    ui.Button("Server Settings", width=120, clicked_fn=self._toggle_settings)
                    ui.Label("  - ", width=0)
                    self._settings_status_label = ui.Label("Checking...", style={"color": _COLOR_CHECKING})

                self._settings_content_frame = ui.Frame(height=0, visible=False)
                with self._settings_content_frame:
                    with ui.VStack(spacing=6, height=0):
                        with ui.HStack(height=24):
                            self._local_checkbox = ui.CheckBox(width=20)
                            ui.Label("Local (WSL2)", width=110)
                            self._remote_checkbox = ui.CheckBox(width=20)
                            ui.Label("Remote")
                        self._local_checkbox.model.add_value_changed_fn(
                            lambda m: self._on_mode_toggled(MODE_LOCAL, m.get_value_as_bool())
                        )
                        self._remote_checkbox.model.add_value_changed_fn(
                            lambda m: self._on_mode_toggled(MODE_REMOTE, m.get_value_as_bool())
                        )

                        self._local_frame = ui.Frame(height=0)
                        with self._local_frame:
                            self._install_button = ui.Button(
                                "Install Local Environment", height=24, clicked_fn=self._install_clicked_fn
                            )

                        self._remote_frame = ui.Frame(height=0)
                        with self._remote_frame:
                            with ui.VStack(spacing=6, height=0):
                                ui.Label("Server IP:")
                                self._host_field = ui.StringField(height=24)
                                self._host_field.model.set_value(self._initial_host)

                        ui.Button("Connect", height=24, clicked_fn=self._connect_clicked_fn)

                ui.Spacer(height=8)
                with ui.HStack(height=24):
                    self._legend_checkbox = ui.CheckBox(width=20)
                    self._legend_checkbox.model.set_value(True)
                    ui.Label("Legend UI")
                self._legend_checkbox.model.add_value_changed_fn(
                    lambda m: self._legend_toggled_fn(m.get_value_as_bool())
                )

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
                # Save the latest request's downloaded results somewhere
                # permanent; enabled only once that result exists.
                with ui.HStack(height=24, spacing=6):
                    self._save_boundary_button = ui.Button(
                        "Save Boundary (.vtp)", clicked_fn=lambda: self._save_clicked_fn("boundary"), enabled=False
                    )
                    self._save_volume_button = ui.Button(
                        "Save Volume (.vti)", clicked_fn=lambda: self._save_clicked_fn("volume"), enabled=False
                    )

                ui.Spacer(height=8)
                self._status_label = ui.Label("Idle.", word_wrap=True)

        self._apply_mode_to_widgets()

    def _apply_mode_to_widgets(self) -> None:
        self._updating_mode = True
        try:
            self._local_checkbox.model.set_value(self._mode == MODE_LOCAL)
            self._remote_checkbox.model.set_value(self._mode == MODE_REMOTE)
        finally:
            self._updating_mode = False
        self._local_frame.visible = self._mode == MODE_LOCAL
        self._remote_frame.visible = self._mode == MODE_REMOTE

    def _on_mode_toggled(self, mode: str, checked: bool) -> None:
        """Local and Remote behave as a pair: turning one on turns the other
        off, and turning the active one off is ignored (one mode is always
        selected)."""
        if self._updating_mode:
            return
        if checked and mode != self._mode:
            self._mode = mode
            self._apply_mode_to_widgets()
            self._mode_changed_fn(mode)
        elif not checked and mode == self._mode:
            self._apply_mode_to_widgets()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def stl_path(self) -> str:
        return self._stl_path_field.model.get_value_as_string()

    @stl_path.setter
    def stl_path(self, value: str) -> None:
        self._stl_path_field.model.set_value(value)

    @property
    def host(self) -> str:
        return self._host_field.model.get_value_as_string()

    @property
    def compute_faces(self) -> bool:
        return self._faces_checkbox.model.get_value_as_bool()

    @property
    def compute_streamlines(self) -> bool:
        return self._streamlines_checkbox.model.get_value_as_bool()

    def _toggle_settings(self) -> None:
        self._settings_content_frame.visible = not self._settings_content_frame.visible

    def set_connection_status(self, connected: bool | None) -> None:
        """connected=True -> "Connect" (green), False -> "Disconnect"
        (red), None -> "Checking..." (grey, while a check is in flight)."""
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

    def set_save_enabled(self, boundary: bool, volume: bool) -> None:
        self._save_boundary_button.enabled = boundary
        self._save_volume_button.enabled = volume

    def set_install_busy(self, busy: bool) -> None:
        self._install_button.enabled = not busy
        self._install_button.text = "Installing..." if busy else "Install Local Environment"
        self.set_busy(busy)
