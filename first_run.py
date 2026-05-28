import json
import shutil
import subprocess
from pathlib import Path
from packaging.version import Version

REQUIRED_VERSION = Version("1.6.5")

STATE_FILE = Path("run_state.json")


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "first_run": True
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)


def get_distro_package_manager():
    managers = {
        "apt": ["sudo", "apt", "install", "-y", "pipewire"],
        "pacman": ["sudo", "pacman", "-S", "--noconfirm", "pipewire"],
        "dnf": ["sudo", "dnf", "install", "-y", "pipewire"],
    }

    for manager in managers:
        if shutil.which(manager):
            return managers[manager]

    return None


def install_or_update_pipewire():
    command = get_distro_package_manager()

    if not command:
        print("Unsupported distro/package manager")
        return False

    try:
        subprocess.run(command, check=True)
        print("PipeWire installed/updated successfully")
        return True

    except subprocess.CalledProcessError:
        print("Failed to install/update PipeWire")
        return False


def check_pipewire():
    state = load_state()

    if not state["first_run"]:
        return

    pipewire_installed = shutil.which("pipewire") is not None
    needs_update = False
    version = None

    if pipewire_installed:
        try:
            result = subprocess.run(
                ["pipewire", "--version"],
                capture_output=True,
                text=True
            )

            for line in result.stdout.splitlines():
                if "libpipewire" in line:
                    version_text = line.split()[-1]
                    version = Version(version_text)
                    break

            if version is None or version < REQUIRED_VERSION:
                needs_update = True

        except Exception:
            needs_update = True

    else:
        needs_update = True

    if needs_update:
        response = input(
            "PipeWire is missing or outdated. Install/update now? [Y/n]: "
        ).strip().lower()

        if response in ("", "y", "yes"):
            install_or_update_pipewire()

    else:
        print(f"PipeWire OK ({version})")

    state["first_run"] = False
    save_state(state)
