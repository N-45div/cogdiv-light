"""PlatformIO post-build step: merge bootloader, partition table, boot_app0 and the app into one
file at offset 0, so it can be flashed from a browser in one go (see README.md)."""

import os

Import("env")  # noqa: F821  (provided by PlatformIO)

OUT = os.path.join(env.subst("$PROJECT_DIR"), "dist", "windbuddy-explorer.bin")  # noqa: F821


def merge(source, target, env):
    images = [(offset, env.subst(path)) for offset, path in env.get("FLASH_EXTRA_IMAGES", [])]
    images.append((env.subst("$ESP32_APP_OFFSET"), str(target[0])))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    parts = " ".join(f'{offset} "{path}"' for offset, path in images)
    env.Execute(
        f'"$PYTHONEXE" "$OBJCOPY" --chip esp32s3 merge_bin -o "{OUT}" '
        f"--flash_mode keep --flash_freq keep --flash_size 16MB {parts}"
    )


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", merge)  # noqa: F821
