![AnyDistro Disk Imager Logo](smalllogo.png)
# AnyDistro Disk Imager

A lightweight, cross-platform disk imaging tool for Linux distributions.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/badge/release-v0.1.1-blue)](https://github.com/ArogueModder/AnyDistro-Disk-Imager/releases/tag/0.1.1)

## Features

- **Simple & Intuitive GUI** – Read or Write disk images to any drive or SD card with ease
- **Cross-Distribution Support** – Works on any Linux distribution
- **Safe Verification** – Built-in checksums to ensure data integrity
- **Progress Tracking** – Real-time feedback during imaging operations
- **Portable AppImage** – Run directly without installation using the AppImage format

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| PyGObject | ≥3.42.0 | GTK 3 bindings for the GUI |
| playsound | ≥1.2.2 | Audio feedback notifications |
| GTK | 3.0+ | Widget toolkit for the interface |


### Build Requirements

- Python 3.8+
- linuxdeploy and linuxdeploy-plugin-appimage
- All runtime dependencies (see `requirements.txt`)

## Installation

### Option 1: AppImage (Recommended)

The easiest way to run AnyDistro Disk Imager is via AppImage—no installation required:

# For x86_64
wget https://github.com/ArogueModder/AnyDistro-Disk-Imager/releases/download/0.1.0/AnyDistro_Disk_Imager-x86_64.AppImage

chmod +x AnyDistro_Disk_Imager-x86_64.AppImage

./AnyDistro_Disk_Imager-x86_64.AppImage

# For aarch64 (ARM64)
wget https://github.com/ArogueModder/AnyDistro-Disk-Imager/releases/download/0.1.0/AnyDistro_Disk_Imager-aarch64.AppImage

chmod +x AnyDistro_Disk_Imager-aarch64.AppImage

./AnyDistro_Disk_Imager-aarch64.AppImage

### Option 2: From Source

#### Requirements

Before building, ensure you have the following packages installed:

**On Ubuntu/Debian:**

    sudo apt update
    sudo apt install -y \
    build-essential \
    ninja-build \
    pkg-config \
    python3.11 \
    python3.11-dev \
    libgirepository2.0-dev \
    libglib2.0-dev \
    libgtk-3-dev \
    gobject-introspection \
    git

**On Fedora/RHEL:**

    sudo dnf groupinstall -y "Development Tools"
    sudo dnf install -y \
    ninja-build \
    pkg-config \
    python3-devel \
    gobject-introspection-devel \
    glib2-devel \
    gtk3-devel \
    git
    
**On Arch Linux:**

    sudo pacman -S --noconfirm \
    base-devel \
    ninja \
    pkg-config \
    python \
    gobject-introspection \
    glib2 \
    gtk3 \
    git
    
#### Build Steps

    git clone https://github.com/ArogueModder/AnyDistro-Disk-Imager.git
    cd AnyDistro-Disk-Imager
    pip install --upgrade pip
    pip install -r requirements.txt
    run build_appimagex86.sh or build_appimageaarch64.sh

**Or run directly without installation:**

    python3 anydistro_disk_imager/main.py

### Graphical Interface

After launching the application, the GUI will guide you through:

1. **Select Image** - Create or choose your `.iso` or `.img` file
2. **Select Target** - Pick your drive or SD card (be careful—data will be overwritten)
3. **Verify** - Verify checksums of disks or images
4. **Write** - Begin writing the image to disk
5. **Read** - Begin reading the disk image to file
6. **Clone** - Clone a disk to a disk of equal or greater size

## System Requirements

- **OS:** Linux (any distribution)
- **Architecture:** x86_64 or aarch64 (or native for your platform)
- **RAM:** Minimal (75 MB for operation)
- **Storage:** ~75 MB for installation

## Troubleshooting

### AppImage won't run: "Permission denied"

Ensure the file is executable:

chmod +x AnyDistro_Disk_Imager-x86_64.AppImage

### Module not found errors

If running from source, ensure your virtual environment is activated and all dependencies are installed:

source venv/bin/activate
pip install -r requirements.txt

### GUI doesn't appear

Verify GTK 3 is installed on your system:

#### Debian/Ubuntu
sudo apt install libgtk-3-0 gir1.2-gtk-3.0

#### Fedora/RHEL
sudo dnf install gtk3

#### Arch
sudo pacman -S gtk3

## License

This project is licensed under the GNU General Public License v3.0 — see the LICENSE file for details.

## Changelog

### Version 0.1.1
- Fixed GTK DPI scaling issues

### Version 0.1.0
- Initial stable release
- AppImage packaging support
- Cross-distribution compatibility

See CHANGELOG.md for full version history.

## Support & Reporting Issues

Found a bug? Have a suggestion? Please open an Issue on GitHub.

When reporting issues, include:
- Your OS and distribution
- Python version (python3 --version)
- Error messages or logs
- Steps to reproduce

## Maintainers

- ArogueModder https://github.com/ArogueModder

## Acknowledgments

- Built with GTK 3
- AppImage packaging via linuxdeploy
- Community feedback and contributions

Made with love for the Linux community

