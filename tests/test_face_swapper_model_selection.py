import importlib
import os
import sys
import tempfile
import types
import unittest


def _install_import_stubs(model_loads):
    sys.modules["cv2"] = types.SimpleNamespace(
        IMREAD_COLOR=1,
        imdecode=lambda *_args, **_kwargs: None,
        imencode=lambda *_args, **_kwargs: (
            True,
            types.SimpleNamespace(tofile=lambda *_args, **_kwargs: None),
        ),
        ellipse=lambda *_args, **_kwargs: None,
        GaussianBlur=lambda src, *_args, **_kwargs: src,
        error=Exception,
    )
    sys.modules["numpy"] = types.SimpleNamespace(
        ndarray=object,
        uint8=object,
        float32=object,
        fromfile=lambda *_args, **_kwargs: types.SimpleNamespace(size=0),
        empty=lambda *_args, **_kwargs: object(),
        zeros=lambda *_args, **_kwargs: object(),
        array=lambda *_args, **_kwargs: object(),
    )
    sys.modules["insightface"] = types.SimpleNamespace(
        model_zoo=types.SimpleNamespace(
            get_model=lambda path, providers=None: model_loads.append((path, providers))
            or types.SimpleNamespace(
                input_size=(128, 128),
                input_names=["input", "latent"],
                output_names=["output"],
                session=types.SimpleNamespace(),
            )
        )
    )
    sys.modules["modules.core"] = types.SimpleNamespace(
        update_status=lambda *_args, **_kwargs: None
    )
    sys.modules["modules.typing"] = types.SimpleNamespace(Face=object, Frame=object)
    sys.modules["modules.face_analyser"] = types.SimpleNamespace(
        get_one_face=lambda *_args, **_kwargs: None,
        get_many_faces=lambda *_args, **_kwargs: None,
        default_source_face=lambda *_args, **_kwargs: None,
    )
    sys.modules["modules.processors.frame.core"] = types.SimpleNamespace()
    sys.modules["modules.utilities"] = types.SimpleNamespace(
        conditional_download=lambda *_args, **_kwargs: None,
        is_image=lambda *_args, **_kwargs: False,
        is_video=lambda *_args, **_kwargs: False,
    )
    sys.modules["modules.cluster_analysis"] = types.SimpleNamespace(
        find_closest_centroid=lambda *_args, **_kwargs: (0, None)
    )
    sys.modules["modules.gpu_processing"] = types.SimpleNamespace(
        gpu_gaussian_blur=lambda src, *_args, **_kwargs: src,
        gpu_sharpen=lambda src, *_args, **_kwargs: src,
        gpu_add_weighted=lambda *_args, **_kwargs: object(),
        gpu_resize=lambda src, *_args, **_kwargs: src,
    )


def _load_face_swapper(model_loads):
    _install_import_stubs(model_loads)
    sys.modules.pop("modules.processors.frame.face_swapper", None)
    face_swapper = importlib.import_module("modules.processors.frame.face_swapper")
    face_swapper.modules.globals.face_swapper_model = None
    face_swapper.FACE_SWAPPER = None
    face_swapper.FACE_SWAPPER_MODEL_PATH = None
    face_swapper._HAS_TORCH_CUDA = False
    face_swapper.IS_APPLE_SILICON = False
    return face_swapper


class FaceSwapperModelSelectionTests(unittest.TestCase):
    def test_lists_only_swapper_onnx_models(self):
        face_swapper = _load_face_swapper([])
        with tempfile.TemporaryDirectory() as models_dir:
            for file_name in (
                "inswapper_128.onnx",
                "inswapper_custom.onnx",
                "custom_swapper.onnx",
                "custom_model.onnx",
                "hyperswap_1c_256.onnx",
                "gfpgan-1024.onnx",
                "gpen_bfr_512.onnx",
                "det_10g.onnx",
                "notes.txt",
            ):
                open(os.path.join(models_dir, file_name), "w").close()
            face_swapper.models_dir = models_dir

            self.assertEqual(
                face_swapper.list_face_swapper_models(),
                ["inswapper_128.onnx", "inswapper_custom.onnx"],
            )

    def test_model_list_reflects_current_models_directory(self):
        face_swapper = _load_face_swapper([])
        with tempfile.TemporaryDirectory() as models_dir:
            face_swapper.models_dir = models_dir

            self.assertEqual(face_swapper.list_face_swapper_models(), [])

            open(os.path.join(models_dir, "inswapper_new.onnx"), "w").close()
            self.assertEqual(face_swapper.list_face_swapper_models(), ["inswapper_new.onnx"])

            os.remove(os.path.join(models_dir, "inswapper_new.onnx"))
            self.assertEqual(face_swapper.list_face_swapper_models(), [])

    def test_auto_selection_preserves_existing_fp16_then_fp32_preference(self):
        face_swapper = _load_face_swapper([])
        with tempfile.TemporaryDirectory() as models_dir:
            fp16_path = os.path.join(models_dir, "inswapper_128_fp16.onnx")
            fp32_path = os.path.join(models_dir, "inswapper_128.onnx")
            open(fp16_path, "w").close()
            open(fp32_path, "w").close()
            face_swapper.models_dir = models_dir

            face_swapper._HAS_TORCH_CUDA = True
            self.assertEqual(face_swapper.resolve_face_swapper_model_path(), fp16_path)

            face_swapper._HAS_TORCH_CUDA = False
            self.assertEqual(face_swapper.resolve_face_swapper_model_path(), fp32_path)

    def test_selected_model_path_must_exist(self):
        face_swapper = _load_face_swapper([])
        with tempfile.TemporaryDirectory() as models_dir:
            model_path = os.path.join(models_dir, "inswapper_custom.onnx")
            open(model_path, "w").close()
            face_swapper.models_dir = models_dir

            face_swapper.modules.globals.face_swapper_model = "inswapper_custom.onnx"
            self.assertEqual(face_swapper.resolve_face_swapper_model_path(), model_path)

            face_swapper.modules.globals.face_swapper_model = "inswapper_missing.onnx"
            self.assertIsNone(face_swapper.resolve_face_swapper_model_path())

    def test_unsupported_selected_model_falls_back_to_auto(self):
        face_swapper = _load_face_swapper([])
        with tempfile.TemporaryDirectory() as models_dir:
            open(os.path.join(models_dir, "hyperswap_1c_256.onnx"), "w").close()
            face_swapper.models_dir = models_dir

            face_swapper.modules.globals.face_swapper_model = "hyperswap_1c_256.onnx"

            self.assertIsNone(face_swapper.resolve_face_swapper_model_path())
            self.assertIsNone(face_swapper.modules.globals.face_swapper_model)

    def test_model_change_invalidates_cache_and_loads_selected_path(self):
        model_loads = []
        face_swapper = _load_face_swapper(model_loads)
        with tempfile.TemporaryDirectory() as models_dir:
            first_path = os.path.join(models_dir, "inswapper_first.onnx")
            second_path = os.path.join(models_dir, "inswapper_second.onnx")
            open(first_path, "w").close()
            open(second_path, "w").close()
            face_swapper.models_dir = models_dir

            face_swapper.set_face_swapper_model("inswapper_first.onnx")
            first_model = face_swapper.get_face_swapper()
            self.assertIsNotNone(first_model)
            self.assertEqual([load[0] for load in model_loads], [first_path])

            self.assertIs(face_swapper.get_face_swapper(), first_model)
            self.assertEqual([load[0] for load in model_loads], [first_path])

            face_swapper.set_face_swapper_model("inswapper_second.onnx")
            self.assertIsNone(face_swapper.FACE_SWAPPER)

            second_model = face_swapper.get_face_swapper()
            self.assertIsNotNone(second_model)
            self.assertEqual([load[0] for load in model_loads], [first_path, second_path])


if __name__ == "__main__":
    unittest.main()
