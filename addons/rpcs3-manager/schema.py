"""Curated per-game config schema — mirrors the RPCS3 UI tabs.

Enum strings are the EXACT serializations from RPCS3's
rpcs3/Emu/system_config_types.cpp; keys/sections match the YAML written by
RPCS3 (verified against real configs). All values are handled as strings
end-to-end (see ryaml.py). Every field also shows the current *global*
config value as its default in the UI.
"""

def f(section, key, type_, **kw):
    d = {"id": "/".join([*section, key]), "section": section, "key": key, "type": type_}
    d.update(kw)
    return d


SCHEMA = [
    {"id": "cpu", "label": "CPU", "fields": [
        f(["Core"], "PPU Decoder", "enum",
          options=["Interpreter (static)", "Recompiler (LLVM)"]),
        f(["Core"], "SPU Decoder", "enum",
          options=["Interpreter (static)", "Interpreter (dynamic)",
                   "Recompiler (ASMJIT)", "Recompiler (LLVM)"]),
        f(["Core"], "SPU XFloat Accuracy", "enum",
          options=["Accurate", "Approximate", "Relaxed", "Inaccurate"],
          hint="Accurate fixes some games at a big performance cost"),
        f(["Core"], "SPU Block Size", "enum", options=["Safe", "Mega", "Giga"]),
        f(["Core"], "Preferred SPU Threads", "int", min=0, max=6,
          hint="0 = auto"),
        f(["Core"], "Max SPURS Threads", "int", min=1, max=6),
        f(["Core"], "SPU loop detection", "bool"),
        f(["Core"], "Clocks scale", "int", min=10, max=1000,
          hint="% of PS3 clock — changes game speed"),
        f(["Core"], "Sleep Timers Accuracy", "enum",
          options=["As Host", "Usleep Only", "All Timers"]),
    ]},
    {"id": "gpu", "label": "GPU", "fields": [
        f(["Video"], "Renderer", "enum", options=["Vulkan", "OpenGL", "Null"]),
        f(["Video"], "Resolution", "enum",
          options=["1280x720", "1920x1080", "720x480", "720x576",
                   "1600x1080", "1440x1080", "1280x1080", "960x1080"],
          hint="PS3 output resolution — most games need 1280x720"),
        f(["Video"], "Aspect ratio", "enum", options=["16:9", "4:3"]),
        f(["Video"], "Frame limit", "enum",
          options=["Off", "30", "50", "60", "Auto", "PS3 Native", "Infinite"]),
        f(["Video"], "Resolution Scale", "int", min=25, max=800,
          hint="100% = native; 200% ≈ 1440p from 720p"),
        f(["Video"], "Anisotropic Filter Override", "enum",
          options=["0", "2", "4", "8", "16"], hint="0 = automatic"),
        f(["Video"], "MSAA", "enum", options=["Auto", "Disabled"]),
        f(["Video"], "Shader Mode", "enum",
          options=["Legacy Recompiler (single-threaded)",
                   "Async Recompiler (multi-threaded)",
                   "Async Recompiler with Shader Interpreter",
                   "Shader Interpreter only"]),
        f(["Video"], "Output Scaling Mode", "enum",
          options=["Nearest", "Bilinear", "FidelityFX Super Resolution"]),
        f(["Video"], "VSync", "bool"),
        f(["Video"], "Stretch To Display Area", "bool"),
        f(["Video"], "Strict Rendering Mode", "bool"),
        f(["Video"], "Write Color Buffers", "bool"),
        f(["Video"], "Read Color Buffers", "bool"),
        f(["Video"], "Read Depth Buffer", "bool"),
        f(["Video"], "Write Depth Buffer", "bool"),
        f(["Video"], "Multithreaded RSX", "bool"),
        f(["Video"], "Relaxed ZCULL Sync", "bool"),
        f(["Video", "Vulkan"], "Asynchronous Texture Streaming 2", "bool"),
        f(["Video", "Vulkan"], "FidelityFX CAS Sharpening Intensity", "int",
          min=0, max=100),
    ]},
    {"id": "audio", "label": "Audio", "fields": [
        f(["Audio"], "Renderer", "enum", options=["Cubeb", "FAudio", "Null"]),
        f(["Audio"], "Audio Format", "enum",
          options=["Stereo", "Surround 5.1", "Surround 7.1", "Automatic", "Manual"]),
        f(["Audio"], "Master Volume", "int", min=0, max=200),
        f(["Audio"], "Enable Buffering", "bool"),
        f(["Audio"], "Desired Audio Buffer Duration", "int", min=4, max=250,
          hint="milliseconds"),
        f(["Audio"], "Enable Time Stretching", "bool"),
    ]},
    {"id": "advanced", "label": "Advanced", "fields": [
        f(["Core"], "PPU Threads", "int", min=1, max=8),
        f(["Core"], "LLVM Precompilation", "bool"),
        f(["Core"], "SPU Cache", "bool"),
        f(["Core"], "Accurate RSX reservation access", "bool"),
        f(["Core"], "Accurate SPU Reservations", "bool"),
        f(["Core"], "Set DAZ and FTZ", "bool"),
        f(["Video"], "Vblank Rate", "int", min=1, max=500,
          hint="60 = default; raising it speeds up some engines"),
        f(["Video"], "Vblank NTSC Fixup", "bool"),
        f(["Video"], "Driver Wake-Up Delay", "int", min=0, max=7000),
        f(["Video"], "Disable Vertex Cache", "bool"),
        f(["Video"], "Disable On-Disk Shader Cache", "bool"),
    ]},
]

FIELD_BY_ID = {fld["id"]: fld for grp in SCHEMA for fld in grp["fields"]}
