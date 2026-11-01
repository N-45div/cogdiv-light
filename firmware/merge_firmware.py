"""PlatformIO post-build step, writing into dist/:

- windbuddy-explorer.bin: bootloader, partition table, boot_app0 and the app merged at offset 0,
  for the first flash from a browser (see README.md);
- windbuddy-app.bin: the app alone, for over-the-air updates through the server;
- manifest.json: the app's version, size and MD5, which the server and the device check.
"""

import hashlib
import json
import os
import shutil

Import("env")  # noqa: F821  (provided by PlatformIO)

DIST = os.path.join(env.subst("$PROJECT_DIR"), "dist")  # noqa: F821
MERGED = os.path.join(DIST, "windbuddy-explorer.bin")
APP = os.path.join(DIST, "windbuddy-app.bin")


def _version(env) -> str:
    for define in env.get("CPPDEFINES", []):
        if isinstance(define, (list, tuple)) and define[0] == "FW_VERSION":
            return str(define[1]).strip('\\"')
    return "dev"


def merge(source, target, env):
    images = [(offset, env.subst(path)) for offset, path in env.get("FLASH_EXTRA_IMAGES", [])]
    images.append((env.subst("$ESP32_APP_OFFSET"), str(target[0])))
    os.makedirs(DIST, exist_ok=True)
    parts = " ".join(f'{offset} "{path}"' for offset, path in images)
    env.Execute(
        f'"$PYTHONEXE" "$OBJCOPY" --chip esp32s3 merge_bin -o "{MERGED}" '
        f"--flash_mode keep --flash_freq keep --flash_size 16MB {parts}"
    )
    shutil.copyfile(str(target[0]), APP)
    with open(APP, "rb") as f:
        data = f.read()
    manifest = {
        "version": _version(env),
        "app": os.path.basename(APP),
        "size": len(data),
        "md5": hashlib.md5(data).hexdigest(),
    }
    with open(os.path.join(DIST, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"Firmware {manifest['version']}: {manifest['size']} bytes, md5 {manifest['md5']}")


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", merge)  # noqa: F821
