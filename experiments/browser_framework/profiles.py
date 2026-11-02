"""Fail-closed profile gate; never read/export cookies or alter source profiles."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def validate_profiles(config_path, *, require_verified=True):
    if not config_path:
        raise ValueError("NOT COMPARABLE: verified private benchmark profile configuration required")
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if require_verified:
        if (config.get("session_equivalence") not in ("MANUALLY_VERIFIED", "BOUNDED_ACCESS_VERIFIED")
                or not config.get("verified_at") or not config.get("common_seed_id")):
            raise ValueError("NOT COMPARABLE: session equivalence has not been independently verified")
        if config["session_equivalence"] == "BOUNDED_ACCESS_VERIFIED":
            import hashlib
            evidence = Path(config_path).with_name("session-validation.json")
            if not evidence.is_file() or hashlib.sha256(evidence.read_bytes()).hexdigest() != config.get("validation_sha256"):
                raise ValueError("NOT COMPARABLE: paired session validation evidence missing or changed")
            result = json.loads(evidence.read_text(encoding="utf-8"))
            if result.get("status") != "PASS" or result.get("common_seed_id") != config["common_seed_id"]:
                raise ValueError("NOT COMPARABLE: paired session validation did not pass")
    profiles = {}
    personal = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Google/Chrome/User Data"
    for name in ("seleniumbase", "playwright"):
        path = Path(config[name]).resolve()
        if path.is_relative_to(ROOT) or path.is_relative_to(personal.resolve()):
            raise ValueError("Use private benchmark copies outside repository and personal Chrome data")
        if not (path / "Local State").is_file() or not (path / "Default/Preferences").is_file():
            raise ValueError("Private benchmark profile missing required structure")
        profiles[name] = path
    if profiles["seleniumbase"] == profiles["playwright"] or any(
            profiles[a].is_relative_to(profiles[b]) for a, b in
            (("seleniumbase", "playwright"), ("playwright", "seleniumbase"))):
        raise ValueError("Frameworks require separate non-overlapping profile copies")
    assert_available(profiles.values())
    return profiles


def assert_available(profiles):
    import psutil
    targets = {os.path.normcase(str(Path(p).resolve())) for p in profiles}
    for process in psutil.process_iter(["name"]):
        if (process.info["name"] or "").casefold() not in ("chrome", "chrome.exe"):
            continue
        try:
            args = process.cmdline()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            raise ValueError("Cannot verify profile availability; refusing access") from None
        for index, arg in enumerate(args):
            value = (arg.split("=", 1)[1] if arg.startswith("--user-data-dir=") else
                     args[index + 1] if arg == "--user-data-dir" and index + 1 < len(args) else None)
            if value and os.path.normcase(str(Path(value.strip('"')).resolve())) in targets:
                raise ValueError("Profile is already open; no process was stopped")
