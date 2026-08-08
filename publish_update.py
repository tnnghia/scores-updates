#!/usr/bin/env python3
"""
Tự tạo releases.json và update_manifest.json từ một gói update ZIP.

Cách dùng cơ bản:
    python publish_update.py Scores_update_v2.5.3.zip

Mặc định:
    repo       = tnnghia/scores-updates
    releases   = releases.json
    manifest   = update_manifest.json

Script KHÔNG upload Release lên GitHub.
Hãy tạo tag/Release và upload ZIP trước, sau đó chạy script này.
"""

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path


DEFAULT_REPO = "tnnghia/scores-updates"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_version_from_zip(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path, "r") as archive:
        names = archive.namelist()

        candidates = [
            name for name in names
            if name == "version.json" or name.endswith("/version.json")
        ]

        if not candidates:
            raise SystemExit(
                "Lỗi: Gói ZIP không có version.json."
            )

        # Ưu tiên version.json ở root.
        version_name = (
            "version.json"
            if "version.json" in candidates
            else sorted(candidates, key=lambda x: x.count("/"))[0]
        )

        try:
            data = json.loads(
                archive.read(version_name).decode("utf-8")
            )
        except Exception as error:
            raise SystemExit(
                f"Lỗi: Không đọc được {version_name}: {error}"
            )

    if not isinstance(data, dict):
        raise SystemExit("Lỗi: version.json phải là JSON object.")

    version = str(data.get("version") or "").strip().lstrip("vV")

    if not version:
        raise SystemExit(
            "Lỗi: version.json thiếu trường version."
        )

    data["version"] = version
    return data


def load_json(path: Path, default):
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise SystemExit(
            f"Lỗi: Không đọc được {path}: {error}"
        )


def write_json(path: Path, data):
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )


def normalize_features(value):
    if not isinstance(value, list):
        return []

    result = []

    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)

    return result


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Tính SHA-256, thêm phiên bản vào releases.json "
            "và sinh update_manifest.json."
        )
    )

    parser.add_argument(
        "zip_file",
        help="Đường dẫn gói ZIP, ví dụ Scores_update_v2.5.3.zip"
    )

    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"GitHub owner/repo. Mặc định: {DEFAULT_REPO}"
    )

    parser.add_argument(
        "--releases",
        default="releases.json",
        help="File lưu lịch sử phiên bản."
    )

    parser.add_argument(
        "--manifest",
        default="update_manifest.json",
        help="File manifest của phiên bản mới nhất."
    )

    args = parser.parse_args()

    zip_path = Path(args.zip_file).resolve()

    if not zip_path.is_file():
        raise SystemExit(
            f"Lỗi: Không tìm thấy ZIP: {zip_path}"
        )

    version_info = read_version_from_zip(zip_path)

    version = version_info["version"]
    sha256 = sha256_file(zip_path)

    # Dùng đúng tên file ZIP thực tế.
    asset_name = zip_path.name

    download_url = (
        f"https://github.com/{args.repo}/releases/download/"
        f"v{version}/{asset_name}"
    )

    title = str(
        version_info.get("title")
        or f"Scores v{version}"
    ).strip()

    description = str(
        version_info.get("description")
        or ""
    ).strip()

    released_at = str(
        version_info.get("released_at")
        or ""
    ).strip()

    channel = str(
        version_info.get("channel")
        or "stable"
    ).strip()

    features = normalize_features(
        version_info.get("features")
    )

    release_notes_url = str(
        version_info.get("release_notes_url")
        or ""
    ).strip()

    release_record = {
        "version": version,
        "released_at": released_at,
        "channel": channel,
        "title": title,
        "description": description,
        "features": features,
        "download_url": download_url,
        "release_notes_url": release_notes_url,
        "sha256": sha256,
    }

    releases_path = Path(args.releases)
    manifest_path = Path(args.manifest)

    releases_data = load_json(
        releases_path,
        {
            "latest": version,
            "releases": []
        }
    )

    if not isinstance(releases_data, dict):
        raise SystemExit(
            "Lỗi: releases.json phải là JSON object."
        )

    releases = releases_data.get("releases")

    if not isinstance(releases, list):
        releases = []

    # Nếu chạy lại cho cùng version, thay record cũ thay vì tạo trùng.
    releases = [
        item for item in releases
        if str(
            item.get("version")
            if isinstance(item, dict)
            else ""
        ).strip().lstrip("vV") != version
    ]

    releases.insert(
        0,
        release_record
    )

    releases_data = {
        "latest": version,
        "releases": releases,
    }

    manifest_data = {
        "version": version,
        "title": title,
        "released_at": released_at,
        "channel": channel,
        "description": description,
        "features": features,
        "download_url": download_url,
        "release_notes_url": release_notes_url,
        "sha256": sha256,
    }

    write_json(
        releases_path,
        releases_data
    )

    write_json(
        manifest_path,
        manifest_data
    )

    print()
    print("=" * 68)
    print(f"PHÁT HÀNH v{version}")
    print("=" * 68)
    print(f"ZIP       : {zip_path.name}")
    print(f"SHA-256   : {sha256}")
    print(f"Release   : v{version}")
    print(f"URL       : {download_url}")
    print()
    print(f"Đã cập nhật: {releases_path.resolve()}")
    print(f"Đã cập nhật: {manifest_path.resolve()}")
    print()
    print("Tiếp theo:")
    print("  1. Kiểm tra Release GitHub đã có đúng ZIP ở URL trên.")
    print("  2. Đưa releases.json và update_manifest.json lên nhánh main.")
    print("  3. Trong ứng dụng: Phiên bản & Cập nhật → Kiểm tra lại.")
    print("=" * 68)


if __name__ == "__main__":
    main()
