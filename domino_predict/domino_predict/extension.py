import asyncio
import os
import shutil
import tempfile
import time

import carb
import carb.eventdispatcher
import omni.client.utils as clientutils
import omni.ext
import omni.kit.asset_converter as asset_converter
import omni.usd
from omni.cae.core.commands import execute_command
from omni.cae.schema import viz as cae_viz
from omni.cae.usd_plugins_importers import import_to_stage
from pxr import Gf, Usd, UsdGeom

from . import remote_predict
from .cae_viz_helpers import wait_for_operator, wait_frames
from .legend import ColorLegend
from .predict_window import PredictWindow


# Fixed pMean color range for every prediction Faces, so the same pressure is
# the same color across STLs (Kit-CAE's default auto-rescale would stretch
# each one to its own min/max). Chosen from all 500 AhmedML CFD cases: the max
# is ~0.52 in every case (stagnation point); the absolute min (-2.14) comes from
# a few edge faces, so the low end uses the per-case 0.1% (by area) value
# instead -- -1.0 covers it for 95% of cases. Values outside clamp to the end
# colors.
PMEAN_RANGE_MIN = -1.0
PMEAN_RANGE_MAX = 0.52

# Fixed |UMean| color range for every prediction's Streamlines. From 450
# AhmedML CFD volume fields (processed train/val): median |U| ~1.0 (inlet),
# per-case 99.9th percentile <= 1.60 in 95% of cases; only tiny regions at
# the front edges reach ~1.6-1.8. Use the same domain on CFD streamlines to
# compare them side by side.
UMAG_RANGE_MIN = 0.0
UMAG_RANGE_MAX = 1.6

# Legend bars for the Faces fields a DoMINO prediction contains (yPlusMean is
# CFD-only). Same fixed ranges recommended for comparing cases with Rescale
# Mode "disable": Cp is exactly 2 x pMean; wall shear stress is colored by
# magnitude (Field Selection Mode "vector_magnitude"), 0.007 covers 99% of
# the surface in 95% of the 500 cases.
LEGEND_FIELDS = [
    ("pMean", PMEAN_RANGE_MIN, PMEAN_RANGE_MAX),
    ("static_p__coeffMean", 2 * PMEAN_RANGE_MIN, 2 * PMEAN_RANGE_MAX),
    ("wallShearStressMean (mag)", 0.0, 0.007),
]


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


class DominoPredictExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._request_counter = 0
        self._legend = ColorLegend(LEGEND_FIELDS)
        # Latest successful request: kind -> (downloaded file, dataset prim path)
        self._latest: dict[str, tuple[str, str]] = {}
        settings = remote_predict.load_settings()
        self._window = PredictWindow(
            request_clicked_fn=self._on_request_clicked,
            browse_clicked_fn=self._on_browse_clicked,
            connect_clicked_fn=self._on_connect_clicked,
            mode_changed_fn=self._on_mode_changed,
            install_clicked_fn=self._on_install_clicked,
            legend_toggled_fn=self._on_legend_toggled,
            save_clicked_fn=self._on_save_clicked,
            initial_mode=settings["mode"],
            initial_host=settings["ssh_host"],
            visible=True,
        )
        # The "Legend UI" toggle starts on, so show the legend right away.
        asyncio.ensure_future(self._legend.show_when_ready())
        # Check the remembered mode/server as soon as the extension comes
        # up, instead of leaving the user to guess whether it's reachable
        # until they explicitly hit Connect.
        asyncio.ensure_future(self._do_connect(settings["mode"], settings["ssh_host"]))

    def on_shutdown(self):
        if self._legend:
            self._legend.destroy()
            self._legend = None
        if self._window:
            self._window.visible = False
            self._window.destroy()
            self._window = None

    def _on_legend_toggled(self, on: bool):
        if on:
            asyncio.ensure_future(self._legend.show_when_ready())
        else:
            self._legend.hide()

    def _on_browse_clicked(self):
        import omni.kit.window.filepicker as filepicker

        def on_selected(filename, dirname):
            self._window.stl_path = os.path.join(dirname, filename)
            dialog.hide()

        dialog = filepicker.FilePickerDialog(
            "Select STL",
            allow_multi_selection=False,
            apply_button_label="Select",
            click_apply_handler=lambda filename, dirname: on_selected(filename, dirname),
            file_extension_types=[(".stl", "STL Files")],
        )
        dialog.show()

    def _on_request_clicked(self):
        asyncio.ensure_future(self._do_request())

    def _on_connect_clicked(self):
        asyncio.ensure_future(self._do_connect())

    def _on_mode_changed(self, mode: str):
        # The old status belonged to the other mode -- re-check right away.
        asyncio.ensure_future(self._do_connect())

    def _on_install_clicked(self):
        asyncio.ensure_future(self._do_install())

    async def _do_install(self):
        self._window.set_install_busy(True)
        self._window.set_connection_status(None)
        start = time.monotonic()
        try:
            ok, message = await remote_predict.install_local_environment(progress_cb=self._window.set_status)
        except Exception as e:
            ok, message = False, f"Install failed: {e}"
        finally:
            self._window.set_install_busy(False)
        elapsed = _format_duration(time.monotonic() - start)
        if not ok:
            carb.log_error(f"[domino_predict] {message}")
            self._window.set_connection_status(False)
            self._window.set_status(f"{message} ({elapsed})")
            return
        self._window.set_status(f"{message} ({elapsed})")
        await self._do_connect()

    async def _do_connect(self, mode: str | None = None, host: str | None = None):
        # Called two ways: from the UI (Connect button / mode toggle, mode and
        # host read from the widgets -- the UI is built by then, since the
        # user just clicked in it) and from on_startup, right after
        # construction, with the remembered values. The startup call races
        # the window's own deferred first build (see PredictWindow's AUDIT
        # FIX comment) -- wait_frames keeps `set_connection_status` below
        # from hitting widgets before _build() has created them.
        await wait_frames(3)
        if mode is None:
            mode = self._window.mode
        if host is None:
            host = self._window.host.strip()
        # Remember whatever the user actually chose, connected or not --
        # they explicitly asked for it, not just for successful ones.
        remote_predict.save_settings(mode, host)
        if mode == remote_predict.MODE_REMOTE and not host:
            self._window.set_connection_status(False)
            return
        self._window.set_connection_status(None)
        ok, message = await remote_predict.test_connection(mode, host)
        self._window.set_connection_status(ok)
        if not ok:
            carb.log_warn(f"[domino_predict] {message}")
        # Shown in the status line so the user knows what to fix (e.g. install
        # WSL, or click Install Local Environment) -- and so a stale failure
        # message is replaced once the connection works.
        self._window.set_status(message)

    async def _do_request(self):
        stl_path = self._window.stl_path.strip()
        if not stl_path or not os.path.isfile(stl_path):
            self._window.set_status(f"Error: STL not found: {stl_path}")
            return

        compute_faces = self._window.compute_faces
        compute_streamlines = self._window.compute_streamlines
        if not compute_faces and not compute_streamlines:
            self._window.set_status("Error: select Faces and/or Streamlines first.")
            return

        self._request_counter += 1
        n = self._request_counter

        mode = self._window.mode
        self._window.set_busy(True)
        self._window.set_save_enabled(False, False)
        start = time.monotonic()
        try:
            self._window.set_status("Loading input shape...")
            shape_path = await self._import_input_shape(stl_path, n)

            inference_start = time.monotonic()
            local_surface_path, local_volume_path = await remote_predict.request_prediction(
                stl_path,
                mode=mode,
                compute_faces=compute_faces,
                compute_streamlines=compute_streamlines,
                progress_cb=self._window.set_status,
            )
            inference_seconds = time.monotonic() - inference_start
            ctx = omni.usd.get_context()
            shape_prim = ctx.get_stage().GetPrimAtPath(shape_path)
            if local_surface_path is not None:
                await self._import_prediction(local_surface_path, n)
                # The Faces mesh (colored by the prediction) sits at the
                # exact same location/scale as the plain input shape -- left
                # visible, its opaque default-white material fully occludes
                # the colored prediction underneath, which looked exactly
                # like "the Faces coloring isn't working" even though it was
                # rendering correctly the whole time. Only hide it when
                # Faces is actually shown -- a Streamlines-only request
                # wants the shape visible, since the streamlines flow around it.
                UsdGeom.Imageable(shape_prim).MakeInvisible()
            if local_volume_path is not None:
                await self._import_volume_streamlines(local_volume_path, shape_path, n)
            self._latest = {}
            if local_surface_path is not None:
                self._latest["boundary"] = (local_surface_path, f"/World/DominoPrediction_{n}")
            if local_volume_path is not None:
                self._latest["volume"] = (local_volume_path, f"/World/DominoVolumePrediction_{n}")
            where = "Local" if mode == remote_predict.MODE_LOCAL else "Remote"
            self._window.set_status(
                f"Prediction imported ({where}). Total {_format_duration(time.monotonic() - start)} "
                f"(inference {_format_duration(inference_seconds)})."
            )
        except Exception as e:
            carb.log_error(f"[domino_predict] Prediction request failed: {e}")
            self._window.set_status(f"Error after {_format_duration(time.monotonic() - start)}: {e}")
        finally:
            self._window.set_busy(False)
            # A failed request keeps the previous result saveable.
            self._window.set_save_enabled("boundary" in self._latest, "volume" in self._latest)

    def _on_save_clicked(self, kind: str):
        """Copies the latest request's downloaded result (kind = "boundary"
        or "volume") to a user-chosen file, then points that dataset prim's
        payload at the copy -- the temp download folder can be cleaned up,
        and a scene saved afterwards should reopen with its results."""
        import omni.kit.window.filepicker as filepicker

        if kind not in self._latest:
            self._window.set_status(f"Error: no {kind} result to save yet.")
            return
        src, prim_path = self._latest[kind]
        ext, label = (".vtp", "VTK PolyData") if kind == "boundary" else (".vti", "VTK ImageData")

        def on_apply(filename, dirname):
            dialog.hide()
            if not filename:
                self._window.set_status("Error: enter a file name to save.")
                return
            name = filename if filename.lower().endswith(ext) else filename + ext
            dst = os.path.realpath(os.path.join(dirname, name))
            try:
                if os.path.normcase(dst) != os.path.normcase(os.path.realpath(src)):
                    shutil.copy2(src, dst)
                prim = omni.usd.get_context().get_stage().GetPrimAtPath(prim_path)
                if prim and prim.IsValid():
                    # Same way omni.cae.usd_plugins_importers records it.
                    payloads = prim.GetPayloads()
                    payloads.ClearPayloads()
                    payloads.AddPayload(clientutils.make_file_url_if_possible(clientutils.normalize_url(dst)))
                    prim.Load()
                self._latest[kind] = (dst, prim_path)
                self._window.set_status(f"Saved {kind}: {dst}")
            except Exception as e:
                carb.log_error(f"[domino_predict] Save {kind} failed: {e}")
                self._window.set_status(f"Error saving {kind}: {e}")

        base = "prediction_boundary" if kind == "boundary" else "prediction_volume"
        dialog = filepicker.FilePickerDialog(
            f"Save {'Boundary' if kind == 'boundary' else 'Volume'} Prediction",
            allow_multi_selection=False,
            apply_button_label="Save",
            click_apply_handler=lambda filename, dirname: on_apply(filename, dirname),
            file_extension_options=[(f"*{ext}", f"{label} (*{ext})")],
            current_filename=base,
        )
        dialog.show()

    async def _import_input_shape(self, stl_path: str, n: int) -> str:
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            raise RuntimeError("No open USD stage to import the input shape into")

        shape_path = f"/World/InputShape_{n}"

        # Not a TemporaryDirectory (self-cleaning) -- the stage keeps a live
        # Reference to this file, which must stay resolvable on disk for the
        # rest of the session (re-composition, stage export, etc).
        # realpath expands 8.3 short names (C:/Users/KYEONG~1/...), which
        # Kit's file picker can't open from the stage's asset-path buttons.
        tmp_dir = os.path.realpath(tempfile.mkdtemp(prefix="domino_predict_stl_"))
        converted_usd_path = os.path.join(tmp_dir, "input_shape.usd")
        task_manager = asset_converter.get_instance()
        context = asset_converter.AssetConverterContext()
        task = task_manager.create_converter_task(stl_path, converted_usd_path, None, context)
        if not await task.wait_until_finished():
            raise RuntimeError(f"STL conversion failed: {task.get_error_message()}")

        prim = stage.DefinePrim(shape_path, "Xform")
        prim.GetReferences().AddReference(converted_usd_path)
        await wait_frames(3)
        return shape_path

    async def _import_prediction(self, vtp_path: str, n: int):
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            raise RuntimeError("No open USD stage to import the prediction into")

        UsdGeom.Xform.Define(stage, "/World/CAE")

        dataset_path = f"/World/DominoPrediction_{n}"
        faces_path = f"/World/CAE/Faces_DominoPrediction_{n}"

        dataset_prim = await import_to_stage(vtp_path, dataset_path)
        await wait_frames(3)

        loop = asyncio.get_event_loop()
        dispatcher = carb.eventdispatcher.get_eventdispatcher()

        # The prediction is now evaluated at every face of the original STL
        # and written back onto its own triangle connectivity (see
        # predict_on_stl.py's inference_surface_mesh_on_single_stl /
        # save_surface_mesh_prediction), so CreateCaeVizFaces works directly
        # -- same as the ground-truth boundary_*.vtp examples -- instead of
        # the disconnected-point-cloud CreateCaeVizPoints workaround.
        await wait_for_operator(
            loop, dispatcher, faces_path, "Faces",
            execute_command(
                "CreateCaeVizFaces",
                dataset_path=str(dataset_prim.GetPath()),
                prim_path=faces_path,
            ),
        )
        faces_prim = stage.GetPrimAtPath(faces_path)
        cae_viz.FieldSelectionAPI(faces_prim, "colors").CreateFieldNamesAttr().Set(["pMean"])
        await wait_frames(3)
        # "disable" stops the operator from auto-rescaling back to this
        # dataset's own min/max; its includes target the shader's domain.
        # With "disable" the operator also skips switching the shader's
        # enable_coloring on (its enableIncludes), so do that here too.
        rescale_api = cae_viz.RescaleRangeAPI(faces_prim, "colors")
        rescale_api.GetRescaleModeAttr().Set("disable")
        for target_path in rescale_api.GetIncludesRel().GetTargets():
            target_attr = stage.GetAttributeAtPath(target_path)
            if target_attr:
                target_attr.Set(Gf.Vec2f(PMEAN_RANGE_MIN, PMEAN_RANGE_MAX))
        for target_path in rescale_api.GetEnableIncludesRel().GetTargets():
            target_attr = stage.GetAttributeAtPath(target_path)
            if target_attr:
                target_attr.Set(True)

        # Same pattern as CAE_Examples/AhmedML/run_1/open_ahmed_cae_scene.py's
        # BoundingBox_boundary_1.
        bbox_path = f"/World/CAE/BoundingBox_DominoPrediction_{n}"
        await execute_command(
            "CreateCaeVizBoundingBox",
            dataset_paths=[str(dataset_prim.GetPath())],
            prim_path=bbox_path,
        )
        await wait_frames(3)

    async def _import_volume_streamlines(self, vti_path: str, shape_path: str, n: int):
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            raise RuntimeError("No open USD stage to import the volume prediction into")

        UsdGeom.Xform.Define(stage, "/World/CAE")

        dataset_path = f"/World/DominoVolumePrediction_{n}"
        sl_path = f"/World/CAE/Streamlines_DominoPrediction_{n}"
        sphere_path = f"/World/CAE/SeedSphere_DominoPrediction_{n}"

        dataset_prim = await import_to_stage(vti_path, dataset_path)
        await wait_frames(3)

        # Seed the streamlines just upstream of the imported shape, sized
        # off its own bounds -- same adaptive placement pattern as
        # CAE_Examples/AhmedML/run_1/open_ahmed_cae_scene.py, since the
        # shape (and the flow domain around it) differs per request.
        shape_prim = stage.GetPrimAtPath(shape_path)
        bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
        bounds = bbox_cache.ComputeWorldBound(shape_prim).ComputeAlignedBox()
        bmin, bmax = bounds.GetMin(), bounds.GetMax()
        size = bmax - bmin
        char_length = max(size[0], size[1], size[2])
        seed_pos = [
            bmin[0] - char_length * 0.8,
            (bmin[1] + bmax[1]) * 0.5,
            (bmin[2] + bmax[2]) * 0.5,
        ]
        seed_radius = max(min(size[1], size[2]) * 0.4, 1e-3)

        await execute_command("CreateCaeVizMeshPrim", prim_type="UnitSphere", prim_path=sphere_path)
        await execute_command(
            "TransformPrimSRT", path=sphere_path,
            new_translation=seed_pos,
            new_scale=[seed_radius] * 3,
        )
        await wait_frames(3)
        sphere_prim = stage.GetPrimAtPath(sphere_path)
        UsdGeom.Imageable(sphere_prim).MakeInvisible()

        loop = asyncio.get_event_loop()
        dispatcher = carb.eventdispatcher.get_eventdispatcher()

        await wait_for_operator(
            loop, dispatcher, sl_path, "Streamlines",
            execute_command(
                "CreateCaeVizStreamlines",
                dataset_path=str(dataset_prim.GetPath()),
                prim_path=sl_path,
                type="standard",
            ),
        )
        sl_prim = stage.GetPrimAtPath(sl_path)

        async def _configure():
            cae_viz.DatasetSelectionAPI(sl_prim, "seeds").GetTargetRel().SetTargets({sphere_prim.GetPath()})
            cae_viz.FieldSelectionAPI(sl_prim, "velocities").CreateFieldNamesAttr().Set(["UMean"])
            colors_api = cae_viz.FieldSelectionAPI(sl_prim, "colors")
            colors_api.CreateFieldNamesAttr().Set(["UMean"])
            colors_api.GetModeAttr().Set(cae_viz.Tokens.vector_magnitude)
            # Match edu_samples/domino_test.usd's CFD Streamlines_volume_10:
            # integrate downstream only (default is "both") at width 0.02.
            streamlines_api = cae_viz.StreamlinesAPI(sl_prim)
            streamlines_api.CreateDirectionAttr().Set(cae_viz.Tokens.forward)
            streamlines_api.CreateWidthAttr().Set(0.02)

        await wait_for_operator(loop, dispatcher, sl_path, "Streamlines", _configure())

        # Fixed |UMean| color range on both streamline materials (ScalarColor,
        # AnimatedStreaks), same as the Faces pMean range: "disable" keeps the
        # operator from rescaling to each prediction's own min/max, and with
        # it disabled enable_coloring has to be switched on here.
        rescale_api = cae_viz.RescaleRangeAPI(sl_prim, "colors")
        rescale_api.GetRescaleModeAttr().Set("disable")
        for target_path in rescale_api.GetIncludesRel().GetTargets():
            target_attr = stage.GetAttributeAtPath(target_path)
            if target_attr:
                target_attr.Set(Gf.Vec2f(UMAG_RANGE_MIN, UMAG_RANGE_MAX))
        for target_path in rescale_api.GetEnableIncludesRel().GetTargets():
            target_attr = stage.GetAttributeAtPath(target_path)
            if target_attr:
                target_attr.Set(True)
