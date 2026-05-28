import json
import shutil
import subprocess
from pathlib import Path

REQUIRED_PIPEWIRE_VERSION = (1, 6, 5)

STATE_FILE = Path("run_state.json")

PACKAGE_MAP = {
    "apt": {
        "install": ["sudo", "apt", "install", "-y"],
        "packages": {
            "pipewire": "pipewire",
            "pulseaudio-utils": "pulseaudio-utils",
            "ffmpeg": "ffmpeg",
            "curl": "curl",
            "tk": "python3-tk",
        },
    },

    "pacman": {
        "install": ["sudo", "pacman", "-S", "--noconfirm"],
        "packages": {
            "pipewire": "pipewire",
            "pulseaudio-utils": "libpulse",
            "ffmpeg": "ffmpeg",
            "curl": "curl",
            "tk": "tk",
        },
    },

    "dnf": {
        "install": ["sudo", "dnf", "install", "-y"],
        "packages": {
            "pipewire": "pipewire",
            "pulseaudio-utils": "pulseaudio-utils",
            "ffmpeg": "ffmpeg",
            "curl": "curl",
            "tk": "python3-tkinter",
        },
    },
}


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


def get_package_manager():
    for manager in PACKAGE_MAP:
        if shutil.which(manager):
            return manager

    return None


def is_binary_installed(binary_name):
    return shutil.which(binary_name) is not None


def version_tuple(version_string):
    return tuple(
        int(part)
        for part in version_string.split(".")
        if part.isdigit()
    )


def get_missing_packages(manager):
    missing = []

    package_checks = {
        "pipewire": "pipewire",
        "pulseaudio-utils": "pactl",
        "ffmpeg": "ffmpeg",
        "curl": "curl",
        "tk": None,
    }

    for package, binary in package_checks.items():

        # tkinter check
        if package == "tk":
            try:
                import tkinter

            except ImportError:
                missing.append(
                    PACKAGE_MAP[manager]["packages"][package]
                )

            continue

        # binary checks
        if not is_binary_installed(binary):
            missing.append(
                PACKAGE_MAP[manager]["packages"][package]
            )

    return missing


def check_pipewire_version():
    try:
        result = subprocess.run(
            ["pipewire", "--version"],
            capture_output=True,
            text=True
        )

        for line in result.stdout.splitlines():
            if "libpipewire" in line:
                version_text = line.split()[-1]

                current_version = version_tuple(
                    version_text
                )

                return (
                    current_version >=
                    REQUIRED_PIPEWIRE_VERSION
                )

    except Exception:
        pass

    return False


def install_packages(manager, packages):
    command = (
        PACKAGE_MAP[manager]["install"] +
        packages
    )

    try:
        subprocess.run(command, check=True)

        print(
            "\nPackages installed/updated successfully"
        )

        return True

    except subprocess.CalledProcessError:
        print(
            "\nFailed to install/update packages"
        )

        return False


def check_dependencies():
    state = load_state()

    if not state["first_run"]:
        return

    manager = get_package_manager()

    if not manager:
        print(
            "Unsupported distro/package manager"
        )
        return

    missing_packages = get_missing_packages(manager)

    # PipeWire version check
    if (
        "pipewire" not in missing_packages and
        not check_pipewire_version()
    ):
        missing_packages.append(
            PACKAGE_MAP[manager]["packages"]["pipewire"]
        )

    # Remove duplicates
    missing_packages = list(set(missing_packages))

    if missing_packages:
        print(
            "\nMissing/outdated packages:"
        )

        for package in missing_packages:
            print(f" - {package}")

        response = input(
            "\nInstall/update now? [Y/n]: "
        ).strip().lower()

        if response in ("", "y", "yes"):
            install_packages(
                manager,
                missing_packages
            )

    else:
        print(
            "All packages are up to date/installed"
        )

    state["first_run"] = False
    save_state(state)


if __name__ == "__main__":
    check_dependencies()
