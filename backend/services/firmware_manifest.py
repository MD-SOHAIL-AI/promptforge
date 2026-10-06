"""Versioned, content-addressed ESP32 upload sets owned by the host.

Successful compilation and readiness to flash are separate outcomes. Only a
complete upload set with known offsets can be sealed; missing images fail closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from contextlib import contextmanager, ExitStack
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


@contextmanager
def lock_upload_set(manifest):
    """Hold Windows sharing locks across validation and the upload process.

    The qualified Windows path denies writes/deletion while allowing the uploader
    to open images for reading. Other operating systems remain unqualified.
    """
    paths = verify_upload_set(manifest)
    with ExitStack() as stack:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            import msvcrt
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            create = kernel.CreateFileW
            create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            create.restype = wintypes.HANDLE
            close = kernel.CloseHandle
            close.argtypes = [wintypes.HANDLE]
            close.restype = wintypes.BOOL
            invalid = ctypes.c_void_p(-1).value
            # Denying directory deletion also prevents exchanging the manifest root.
            directory = create(str(Path(manifest["artifact_root"])), 0x80000000, 1, None, 3, 0x02000000, None)
            if directory == invalid:
                raise OSError(ctypes.get_last_error(), "UPLOAD_DIRECTORY_LOCK_UNAVAILABLE")
            stack.callback(close, directory)
            for _, path in paths:
                handle = create(str(path), 0x80000000, 1, None, 3, 0x80, None)
                if handle == invalid:
                    raise OSError(ctypes.get_last_error(), "UPLOAD_IMAGE_LOCK_UNAVAILABLE")
                try:
                    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                except BaseException:
                    close(handle)
                    raise
                stack.enter_context(os.fdopen(descriptor, "rb"))
        # Verify again after all locks are held; cancellation releases every handle.
        verify_upload_set(manifest)
        yield paths


def seal_upload_set(store, *, chip, environment, board, images, toolchain=None):
    if chip not in {"esp32", "esp32s3", "esp32c3"}:
        raise ValueError("UPLOAD_CHIP_UNSUPPORTED")
    entries = []
    content = {}
    for role, offset, path in images:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("UPLOAD_IMAGE_UNAVAILABLE")
        data = path.read_bytes()
        if not data or len(data) > 16 * 1024 * 1024:
            raise ValueError("UPLOAD_IMAGE_SIZE_INVALID")
        sha = hashlib.sha256(data).hexdigest()
        if role in content or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("UPLOAD_IMAGE_INVALID")
        content[role] = data
        entries.append({"role": role, "offset": offset, "sha256": sha, "size_bytes": len(data),
                        "filename": role + ".bin"})
    entries.sort(key=lambda entry: entry["offset"])
    if {item["role"] for item in entries} not in (
            {"bootloader", "partitions", "application"},
            {"bootloader", "partitions", "ota_data", "application"}):
        raise ValueError("UPLOAD_SET_INCOMPLETE")
    for index, entry in enumerate(entries):
        if index and entries[index - 1]["offset"] + entries[index - 1]["size_bytes"] > entry["offset"]:
            raise ValueError("UPLOAD_IMAGES_OVERLAP")
    body = {"schema_version": 1, "chip": chip, "environment": environment, "board": board,
            "toolchain": toolchain or {}, "images": entries}
    identity = digest(body)
    root = Path(store).resolve() / identity
    root.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        target = root / entry["filename"]
        try:
            with target.open("xb") as handle:
                handle.write(content[entry["role"]])
        except FileExistsError:
            if target.is_symlink() or target.read_bytes() != content[entry["role"]]:
                raise ValueError("SEALED_ARTIFACT_TAMPERED")
    manifest = {**body, "sha256": identity, "artifact_root": str(root)}
    verify_upload_set(manifest, store=store)
    # The JSON file is diagnostic; authority stays in the host's persisted run.
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def verify_upload_set(manifest, *, store=None, chip=None, environment=None):
    from backend.contracts.engine import FirmwareUploadManifest
    manifest = FirmwareUploadManifest.model_validate(manifest).model_dump()
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("SEALED_UPLOAD_REQUIRED")
    if set(manifest) != {"schema_version", "chip", "environment", "board", "toolchain", "images", "sha256", "artifact_root"}:
        raise ValueError("UPLOAD_MANIFEST_INVALID")
    body = {key: value for key, value in manifest.items() if key not in {"sha256", "artifact_root"}}
    if digest(body) != manifest["sha256"]:
        raise ValueError("UPLOAD_MANIFEST_TAMPERED")
    if manifest["chip"] not in {"esp32", "esp32s3", "esp32c3"} or (chip and manifest["chip"] != chip):
        raise ValueError("UPLOAD_CHIP_MISMATCH")
    if environment and manifest["environment"] != environment:
        raise ValueError("UPLOAD_ENVIRONMENT_MISMATCH")
    root = Path(manifest["artifact_root"])
    if root.is_symlink() or root.name != manifest["sha256"]:
        raise ValueError("UPLOAD_ROOT_INVALID")
    root = root.resolve(strict=True)
    if store is not None and root.parent != Path(store).resolve(strict=True):
        raise ValueError("UPLOAD_ROOT_INVALID")
    entries = manifest["images"]
    if not isinstance(entries, list) or not 3 <= len(entries) <= 4:
        raise ValueError("UPLOAD_SET_INCOMPLETE")
    roles = set()
    paths = []
    end = 0
    for entry in entries:
        role = entry.get("role")
        if role not in {"application", "bootloader", "partitions", "ota_data"} or role in roles:
            raise ValueError("UPLOAD_IMAGE_INVALID")
        roles.add(role)
        if entry.get("filename") != role + ".bin":
            raise ValueError("UPLOAD_IMAGE_PATH_INVALID")
        offset, size = entry.get("offset"), entry.get("size_bytes")
        if (not isinstance(offset, int) or isinstance(offset, bool) or offset < end
                or not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= 16 * 1024 * 1024
                or offset + size > 16 * 1024 * 1024):
            raise ValueError("UPLOAD_IMAGES_OVERLAP")
        path = root / entry["filename"]
        if path.is_symlink() or path.resolve(strict=True).parent != root:
            raise ValueError("UPLOAD_IMAGE_PATH_INVALID")
        data = path.read_bytes()
        if len(data) != size or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("SEALED_ARTIFACT_TAMPERED")
        paths.append((offset, path))
        end = offset + size
    if not {"application", "bootloader", "partitions"} <= roles:
        raise ValueError("UPLOAD_SET_INCOMPLETE")
    return tuple(paths)


def seal_platformio_build(build, project, environment, store, *, core_dir=None):
    from .platformio_service import _load_project, _discover_from_parser
    root, _, parser = _load_project(project)
    environments = _discover_from_parser(parser)
    artifact = Path(build.firmware_path).resolve(strict=True)
    selected = environment or artifact.parent.name
    config = next((item for item in environments if item.name == selected), None)
    # Additional chips need their own validated offset/profile adapter.
    if config is None or config.board != "esp32dev" or config.framework.lower() != "arduino":
        raise ValueError("UPLOAD_PROFILE_UNSUPPORTED")
    from .platformio_service import _resolve_option
    for option in ("board_upload.offset_address", "board_build.bootloader_addr", "board_build.partitions_offset"):
        if _resolve_option(parser, selected, option):
            raise ValueError("UPLOAD_OFFSET_OVERRIDE_UNSUPPORTED")
    directory = artifact.parent
    if directory != (root / ".pio" / "build" / selected).resolve():
        raise ValueError("UPLOAD_BUILD_PATH_INVALID")
    partitions = directory / "partitions.bin"
    data = partitions.read_bytes()
    parsed = []
    for index in range(0, len(data) - 31, 32):
        magic, kind, subtype, offset, size = struct.unpack_from("<HBBII", data, index)
        if magic == 0x50AA:
            parsed.append((kind, subtype, offset, size))
        elif magic in {0xFFFF, 0xEBEB}:
            break
        else:
            raise ValueError("UPLOAD_PARTITIONS_INVALID")
    applications = [item for item in parsed if item[0] == 0 and item[1] in {0, 0x10}]
    if not applications:
        raise ValueError("UPLOAD_APPLICATION_PARTITION_MISSING")
    application = min(applications, key=lambda item: item[1])
    if artifact.stat().st_size > application[3]:
        raise ValueError("UPLOAD_APPLICATION_TOO_LARGE")
    images = [("bootloader", 0x1000, directory / "bootloader.bin"),
              ("partitions", 0x8000, partitions), ("application", application[2], artifact)]
    ota = [item for item in parsed if item[:2] == (1, 0)]
    if ota:
        if len(ota) != 1 or ota[0][3] != 0x2000:
            raise ValueError("UPLOAD_OTA_PARTITION_UNSUPPORTED")
        core = Path(core_dir or os.environ.get("PLATFORMIO_CORE_DIR", str(Path.home() / ".platformio")))
        images.append(("ota_data", ota[0][2], core / "packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin"))
    return seal_upload_set(store, chip="esp32", environment=selected, board=config.board,
                           images=images, toolchain=build.metadata.get("toolchain"))
