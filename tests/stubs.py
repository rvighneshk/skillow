import json
import os
import sys
import types
from pathlib import Path


def install():
    if "pydantic_settings" not in sys.modules:
        try:
            import pydantic_settings  # noqa: F401
        except ImportError:
            mod = types.ModuleType("pydantic_settings")

            class BaseSettings:
                def __init__(self):
                    for cls in reversed(type(self).__mro__):
                        for name, tp in getattr(cls, "__annotations__", {}).items():
                            default = getattr(type(self), name, None)
                            raw = os.environ.get(name.upper())
                            if raw is None:
                                setattr(self, name, default)
                            elif tp is bool or tp == "bool":
                                setattr(self, name, raw.lower() in ("1", "true", "yes"))
                            elif tp is int or tp == "int":
                                setattr(self, name, int(raw))
                            elif tp is Path or tp == "Path":
                                setattr(self, name, Path(raw))
                            else:
                                setattr(self, name, raw)

            mod.BaseSettings, mod.SettingsConfigDict = BaseSettings, dict
            sys.modules["pydantic_settings"] = mod
    try:
        import meta_sam_parser  # noqa: F401
    except ImportError:
        mod = types.ModuleType("meta_sam_parser")

        class Mask:
            def __init__(self, w, h, bits):
                self.width, self.height, self.bits = w, h, bits

        class Record:
            def __init__(self, kind, mask=None):
                self.kind, self.mask = kind, mask

        class Parser:
            def __init__(self):
                self.text = ""

            def push(self, text):
                self.text += text

            def finish(self, outcome):
                records = [Record(r["kind"], Mask(r["w"], r["h"], r["bits"])) for r in json.loads(self.text or "[]")]
                return types.SimpleNamespace(result=types.SimpleNamespace(records=records, diagnostics=[]))

        mod.CompletedOutcome = lambda: None
        mod.image_segmentation_format = lambda: types.SimpleNamespace(create_parser=Parser)
        mod.records_of_kind = lambda records, kind: [r for r in records if r.kind == kind]
        mod.decode_mask_to_raster = lambda mask: mask.bits
        sys.modules["meta_sam_parser"] = mod
