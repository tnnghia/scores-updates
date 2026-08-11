#!/usr/bin/env python3
r"""Phát hành gói cập nhật Scores bằng một lệnh duy nhất.

Ví dụ:
    python publish_update_auto.py .\packages\Scores_update_v2.6.8.zip --publish --branch main

Script luôn lấy version/requires từ version.json bên trong ZIP, tự cập nhật
update_manifest.json và releases.json, kiểm tra toàn bộ chuỗi trước khi tạo
GitHub Release. Release được tạo ở trạng thái draft, chỉ công khai sau khi
metadata đã commit và push thành công.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class PublishError(RuntimeError):
    pass


def run(command: list[str], cwd: Path, capture: bool = True) -> str:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )
    if result.returncode:
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise PublishError(f"Lệnh thất bại:\n{' '.join(command)}\n{output}")
    return (result.stdout or "").strip()


def normalize_version(value: object, label: str) -> str:
    version = str(value or "").strip().lstrip("vV")
    if not VERSION_RE.fullmatch(version):
        raise PublishError(f"{label} không hợp lệ: {value!r}")
    return version


def version_tuple(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in normalize_version(value, "Phiên bản").split("."))


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as error:
        raise PublishError(f"Không đọc được {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise PublishError(f"{path.name} phải chứa JSON object.")
    return value


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def package_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_package(path: Path) -> dict:
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise PublishError(f"Không tìm thấy file ZIP: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            bad_file = archive.testzip()
            if bad_file:
                raise PublishError(f"ZIP bị hỏng tại: {bad_file}")
            names = [name.replace("\\", "/") for name in archive.namelist()]
            matches = [name for name in names if name == "version.json" or name.endswith("/version.json")]
            if len(matches) != 1:
                raise PublishError("ZIP phải có đúng một file version.json.")
            metadata = json.loads(archive.read(matches[0]).decode("utf-8-sig"))
    except PublishError:
        raise
    except Exception as error:
        raise PublishError(f"Không đọc được gói ZIP: {error}") from error
    if not isinstance(metadata, dict):
        raise PublishError("version.json trong ZIP phải là JSON object.")
    version = normalize_version(metadata.get("version"), "version trong ZIP")
    requires = normalize_version(metadata.get("requires"), f"requires của v{version}")
    if version_tuple(requires) >= version_tuple(version):
        raise PublishError(f"v{version} có requires không hợp lệ: v{requires}.")
    metadata["version"] = version
    metadata["requires"] = requires
    metadata.setdefault("released_at", "")
    metadata.setdefault("channel", "stable")
    metadata.setdefault("title", f"Phiên bản v{version}")
    metadata.setdefault("description", "")
    metadata["features"] = metadata.get("features") if isinstance(metadata.get("features"), list) else []
    return metadata


def release_record(metadata: dict, package_name: str, repository: str, digest: str) -> dict:
    version = metadata["version"]
    return {
        "version": version,
        "requires": metadata["requires"],
        "released_at": str(metadata.get("released_at") or ""),
        "channel": str(metadata.get("channel") or "stable"),
        "title": str(metadata.get("title") or f"Phiên bản v{version}"),
        "description": str(metadata.get("description") or ""),
        "features": [str(item) for item in metadata.get("features", [])],
        "download_url": f"https://github.com/{repository}/releases/download/v{version}/{package_name}",
        "release_notes_url": f"https://github.com/{repository}/releases/tag/v{version}",
        "sha256": digest,
    }


def update_history(history: dict, current: dict) -> dict:
    releases = history.get("releases")
    if not isinstance(releases, list):
        raise PublishError("releases.json thiếu mảng releases.")
    cleaned = []
    replaced = False
    for item in releases:
        if not isinstance(item, dict):
            raise PublishError("releases.json có phần tử không phải object.")
        version = normalize_version(item.get("version"), "version trong releases.json")
        if version == current["version"]:
            if replaced:
                raise PublishError(f"releases.json trùng phiên bản {version}.")
            cleaned.append(dict(current))
            replaced = True
        else:
            cleaned.append(dict(item))
    if not replaced:
        cleaned.append(dict(current))
    cleaned.sort(key=lambda item: version_tuple(str(item.get("version"))), reverse=True)
    return {**history, "latest": current["version"], "releases": cleaned}


def validate_metadata(package: dict, manifest: dict, history: dict, digest: str) -> None:
    version = package["version"]
    requires = package["requires"]
    if normalize_version(manifest.get("version"), "manifest.version") != version:
        raise PublishError("update_manifest.json không khớp version trong ZIP.")
    if normalize_version(manifest.get("requires"), "manifest.requires") != requires:
        raise PublishError("update_manifest.json không khớp requires trong ZIP.")
    manifest_sha = str(manifest.get("sha256") or "").strip().lower()
    if manifest_sha != digest or not SHA_RE.fullmatch(manifest_sha):
        raise PublishError("SHA-256 trong update_manifest.json không khớp ZIP.")
    if normalize_version(history.get("latest"), "releases.latest") != version:
        raise PublishError("latest trong releases.json không khớp ZIP.")

    releases = history.get("releases")
    if not isinstance(releases, list):
        raise PublishError("releases.json thiếu mảng releases.")
    catalog: dict[str, dict] = {}
    for item in releases:
        if not isinstance(item, dict):
            raise PublishError("releases.json có phần tử không phải object.")
        item_version = normalize_version(item.get("version"), "version trong releases.json")
        if item_version in catalog:
            raise PublishError(f"releases.json trùng phiên bản {item_version}.")
        item_requires = normalize_version(item.get("requires"), f"requires của v{item_version}")
        if version_tuple(item_requires) >= version_tuple(item_version):
            raise PublishError(f"requires của v{item_version} không hợp lệ: v{item_requires}.")
        catalog[item_version] = item

    newest = catalog.get(version)
    if not newest:
        raise PublishError(f"releases.json thiếu v{version}.")
    if normalize_version(newest.get("requires"), f"requires của v{version}") != requires:
        raise PublishError(f"requires của v{version} trong releases.json không khớp ZIP.")
    if str(newest.get("sha256") or "").strip().lower() != digest:
        raise PublishError(f"SHA-256 của v{version} trong releases.json không khớp ZIP.")

    visited = set()
    current = version
    while current in catalog:
        if current in visited:
            raise PublishError(f"Chuỗi requires bị lặp tại v{current}.")
        visited.add(current)
        required = normalize_version(catalog[current].get("requires"), f"requires của v{current}")
        if required not in catalog:
            break
        current = required


def repository_name(repo_root: Path, explicit: str | None) -> str:
    if explicit:
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", explicit):
            raise PublishError("--repo phải có dạng owner/repository.")
        return explicit
    remote = run(["git", "remote", "get-url", "origin"], repo_root)
    match = re.search(r"github\.com[/:]([^/\s]+)/([^/\s]+?)(?:\.git)?$", remote)
    if not match:
        raise PublishError("Không suy ra được owner/repository từ git remote origin; hãy dùng --repo.")
    return f"{match.group(1)}/{match.group(2)}"


def git_preflight(repo_root: Path, branch: str) -> None:
    run(["git", "rev-parse", "--show-toplevel"], repo_root)
    current_branch = run(["git", "branch", "--show-current"], repo_root)
    if current_branch != branch:
        raise PublishError(f"Đang ở nhánh {current_branch!r}, không phải {branch!r}.")
    tracked_changes = run(["git", "status", "--porcelain", "--untracked-files=no"], repo_root)
    if tracked_changes:
        raise PublishError("Kho đang có thay đổi tracked chưa commit; hãy xử lý trước:\n" + tracked_changes)
    run(["git", "fetch", "origin", branch], repo_root)
    counts = run(["git", "rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"], repo_root)
    ahead, behind = (int(value) for value in counts.split())
    if ahead or behind:
        raise PublishError(
            f"Nhánh local và origin/{branch} chưa đồng bộ (ahead={ahead}, behind={behind}). "
            "Hãy pull/rebase hoặc push xong trước khi phát hành."
        )


def release_notes(metadata: dict) -> str:
    lines = [str(metadata.get("description") or "").strip(), "", "Nội dung cập nhật:"]
    lines.extend(f"- {item}" for item in metadata.get("features", []))
    return "\n".join(lines).strip() + "\n"


def publish_release(repo_root: Path, package_path: Path, metadata: dict, branch: str, repository: str) -> None:
    version = metadata["version"]
    tag = f"v{version}"
    existing = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repository],
        cwd=str(repo_root), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if existing.returncode == 0:
        raise PublishError(f"GitHub Release {tag} đã tồn tại; dừng để tránh ghi đè ngoài ý muốn.")

    notes_file = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as stream:
            stream.write(release_notes(metadata))
            notes_file = Path(stream.name)
        run([
            "gh", "release", "create", tag, str(package_path), "--repo", repository,
            "--draft", "--title", tag, "--notes-file", str(notes_file),
        ], repo_root)
        release_data = json.loads(run([
            "gh", "release", "view", tag, "--repo", repository, "--json", "isDraft,assets",
        ], repo_root))
        assets = release_data.get("assets") if isinstance(release_data, dict) else []
        if not release_data.get("isDraft") or not any(item.get("name") == package_path.name for item in assets or []):
            raise PublishError("Không xác nhận được draft release và asset vừa tải lên.")

        run(["git", "add", "--", "update_manifest.json", "releases.json"], repo_root)
        run(["git", "commit", "-m", f"Release v{version}: update manifest and release history"], repo_root)
        run(["git", "push", "origin", branch], repo_root)
        run(["gh", "release", "edit", tag, "--repo", repository, "--draft=false"], repo_root)
    except Exception as error:
        raise PublishError(
            f"{error}\nGitHub Release {tag} nếu đã được tạo vẫn ở trạng thái draft và chưa công khai."
        ) from error
    finally:
        if notes_file:
            notes_file.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tạo metadata và phát hành bản cập nhật Scores an toàn.")
    parser.add_argument("package", help="Đường dẫn file Scores_update_vX.Y.Z.zip")
    parser.add_argument("--publish", action="store_true", help="Tạo GitHub Release, commit và push metadata")
    parser.add_argument("--branch", default="main", help="Nhánh Git cần push, mặc định main")
    parser.add_argument("--repo", help="GitHub repository dạng owner/name; mặc định lấy từ origin")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path.cwd().resolve()
    package_path = Path(args.package).expanduser()
    if not package_path.is_absolute():
        package_path = (repo_root / package_path).resolve()
    manifest_path = repo_root / "update_manifest.json"
    history_path = repo_root / "releases.json"
    if not history_path.is_file():
        raise PublishError(f"Không tìm thấy {history_path.name} trong {repo_root}")
    if shutil.which("git") is None:
        raise PublishError("Không tìm thấy git trong PATH.")
    if args.publish and shutil.which("gh") is None:
        raise PublishError("Không tìm thấy GitHub CLI (gh) trong PATH.")

    if args.publish:
        git_preflight(repo_root, args.branch)
    repository = repository_name(repo_root, args.repo)
    package = read_package(package_path)
    digest = package_sha256(package_path)
    current = release_record(package, package_path.name, repository, digest)
    history = update_history(read_json(history_path), current)
    manifest = dict(current)
    validate_metadata(package, manifest, history, digest)
    write_json_atomic(manifest_path, manifest)
    write_json_atomic(history_path, history)

    print(f"[OK] v{package['version']} requires v{package['requires']}")
    print(f"[OK] SHA-256: {digest}")
    print(f"[OK] Đã cập nhật {manifest_path.name} và {history_path.name}")
    if args.publish:
        publish_release(repo_root, package_path, package, args.branch, repository)
        print(f"[OK] Đã công bố v{package['version']} và push origin/{args.branch}")
    else:
        print("[DRY RUN] Chưa tạo GitHub Release. Dùng --publish để phát hành.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as error:
        print(f"Lỗi: {error}", file=sys.stderr)
        raise SystemExit(1)
