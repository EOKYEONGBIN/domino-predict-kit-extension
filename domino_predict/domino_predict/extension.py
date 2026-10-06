import asyncio
import os
import tempfile

import carb
import carb.eventdispatcher
import omni.ext
import omni.kit.asset_converter as asset_converter
import omni.usd
from omni.cae.core.commands import execute_command
from omni.cae.schema import viz as cae_viz
from omni.cae.usd_plugins_importers import import_to_stage
from pxr import Usd, UsdGeom

from . import remote_predict
from .cae_viz_helpers import wait_for_operator, wait_frames
from .predict_window import PredictWindow


class DominoPredictExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._request_counter = 0
        settings = remote_predict.load_settings()
        self._window = PredictWindow(
            request_clicked_fn=self._on_request_clicked,
            browse_clicked_fn=self._on_browse_clicked,
            connect_clicked_fn=self._on_connect_clicked,
            initial_host=settings["ssh_host"],
            visible=True,
        )
        # Check the default/remembered server as soon as the extension comes
        # up, instead of leaving the user to guess whether it's reachable
        # until they explicitly hit Connect.
        asyncio.ensure_future(self._do_connect(settings["ssh_host"]))

    def on_shutdown(self):
        if self._window:
            self._window.visible = False
            self._window.destroy()
            self._window = None

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

    async def _do_connect(self, host: str | None = None):
        # Called two ways: from the Connect button (host=None, read the
        # field -- by then the UI has definitely been built, since the user
        # just clicked a button in it) and from on_startup, right after
        # construction, to auto-check the remembered server (host=<remembered
        # value>). The startup call races the window's own deferred first
        # build (see PredictWindow's AUDIT FIX comment) -- wait_frames keeps
        # `set_connection_status` below from hitting `_settings_frame`
        # before _build() has created it.
        await wait_frames(3)
        if host is None:
            host = self._window.host.strip()
        if not host:
            self._window.set_connection_status(False)
            return
        self._window.set_connection_status(None)
        ok, _message = await remote_predict.test_connection(host)
        self._window.set_connection_status(ok)
        # Remember whatever the user actually typed, connected or not --
        # they explicitly asked for it, not just for successful ones.
        remote_predict.save_settings(host)

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

        self._window.set_busy(True)
        try:
            self._window.set_status("Loading input shape...")
            shape_path = await self._import_input_shape(stl_path, n)

            local_surface_path, local_volume_path = await remote_predict.request_prediction(
                stl_path,
                compute_faces=compute_faces,
                compute_streamlines=compute_streamlines,
                progress_cb=self._window.set_status,
            )
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
            self._window.set_status("Prediction imported.")
        except Exception as e:
            carb.log_error(f"[domino_predict] Prediction request failed: {e}")
            self._window.set_status(f"Error: {e}")
        finally:
            self._window.set_busy(False)

    async def _import_input_shape(self, stl_path: str, n: int) -> str:
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            raise RuntimeError("No open USD stage to import the input shape into")

        shape_path = f"/World/InputShape_{n}"

        # Not a TemporaryDirectory (self-cleaning) -- the stage keeps a live
        # Reference to this file, which must stay resolvable on disk for the
        # rest of the session (re-composition, stage export, etc).
        tmp_dir = tempfile.mkdtemp(prefix="domino_predict_stl_")
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

        await wait_for_operator(loop, dispatcher, sl_path, "Streamlines", _configure())
