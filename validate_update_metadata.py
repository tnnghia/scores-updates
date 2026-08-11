"""Dừng phát hành nếu metadata gói cập nhật thiếu hoặc sai requires.

Cách dùng:
    python validate_update_metadata.py PACKAGE.zip update_manifest.json releases.json
"""

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


def fail(message):
    raise SystemExit(f"LỖI: {message}")


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception as error:
        fail(f"Không đọc được {path}: {error}")


def version_tuple(value):
    text = str(value or "").strip().lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+", text):
        fail(f"Phiên bản không hợp lệ: {value!r}")
    return tuple(int(part) for part in text.split("."))


def required_text(item, source):
    value = str(item.get("requires") or "").strip().lstrip("vV")
    if not value:
        fail(f"{source} thiếu requires cho phiên bản {item.get('version')}.")
    version_tuple(value)
    return value


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    if len(sys.argv) != 4:
        fail("Cú pháp: python validate_update_metadata.py PACKAGE.zip update_manifest.json releases.json")
    package_path, manifest_path, releases_path = map(Path, sys.argv[1:])
    manifest = load_json(manifest_path)
    history = load_json(releases_path)
    try:
        with zipfile.ZipFile(package_path) as archive:
            package = json.loads(archive.read("version.json").decode("utf-8-sig"))
            bad = archive.testzip()
            if bad:
                fail(f"ZIP hỏng tại {bad}.")
    except Exception as error:
        fail(f"Không đọc được version.json trong ZIP: {error}")

    package_version = str(package.get("version") or "").strip().lstrip("vV")
    manifest_version = str(manifest.get("version") or "").strip().lstrip("vV")
    package_requires = required_text(package, "version.json trong ZIP")
    manifest_requires = required_text(manifest, "update_manifest.json")
    if package_version != manifest_version:
        fail(f"ZIP là {package_version} nhưng manifest là {manifest_version}.")
    if package_requires != manifest_requires:
        fail(f"requires không khớp: ZIP={package_requires}, manifest={manifest_requires}.")

    actual_hash = sha256(package_path)
    if str(manifest.get("sha256") or "").strip().lower() != actual_hash:
        fail("SHA-256 trong update_manifest.json không khớp file ZIP.")
    if str(history.get("latest") or "").strip().lstrip("vV") != package_version:
        fail("latest trong releases.json không khớp phiên bản ZIP.")

    release_items = history.get("releases")
    if not isinstance(release_items, list):
        fail("releases.json thiếu mảng releases.")
    catalog = {}
    for item in release_items:
        if not isinstance(item, dict):
            fail("releases.json có phần tử không phải object.")
        version = str(item.get("version") or "").strip().lstrip("vV")
        version_tuple(version)
        if version in catalog:
            fail(f"releases.json trùng phiên bản {version}.")
        catalog[version] = item
        required = required_text(item, "releases.json")
        if version_tuple(required) >= version_tuple(version):
            fail(f"requires của {version} không thấp hơn phiên bản hiện tại: {required}.")

    latest = catalog.get(package_version)
    if not latest:
        fail(f"releases.json thiếu phiên bản mới nhất {package_version}.")
    if required_text(latest, "releases.json") != package_requires:
        fail("requires của bản mới nhất trong releases.json không khớp version.json.")
    if str(latest.get("sha256") or "").strip().lower() != actual_hash:
        fail("SHA-256 của bản mới nhất trong releases.json không khớp ZIP.")

    visited = set()
    current = package_version
    while current in catalog:
        if current in visited:
            fail(f"Chuỗi requires bị lặp tại {current}.")
        visited.add(current)
        required = required_text(catalog[current], "releases.json")
        if required in catalog and required != current:
            current = required
            continue
        break

    print(f"OK: v{package_version} requires v{package_requires}; SHA-256 {actual_hash}")
    print(f"OK: Chuỗi releases.json đã kiểm tra {len(visited)} phiên bản, dừng tại nền v{required}.")


if __name__ == "__main__":
    main()
