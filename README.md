# AnyDistro Disk Imager

A lightweight, cross-platform disk imaging tool for Linux distributions.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue)](https://www.python.org/downloads/)

## Features

- **Simple & Intuitive GUI** – Read or Write disk images to any drive or SD card with ease
- **Cross-Distribution Support** – Works on any Linux distribution
- **Safe Verification** – Built-in checksums to ensure data integrity
- **Progress Tracking** – Real-time feedback during imaging operations
- **Portable AppImage** – Run directly without installation using the AppImage format

## Installation

### Option 1: AppImage (Recommended)

The easiest way to run AnyDistro Disk Imager is via AppImage—no installation required:

wget AnyDistro_Disk_Imager-x86_64.AppImage
chmod +x AnyDistro_Disk_Imager-x86_64.AppImage
./AnyDistro_Disk_Imager-x86_64.AppImage

### Option 2: From Source

#### Requirements

- **Python 3.8 or higher**
- **pip** (Python package manager)
- **GTK 3.0+** (for GUI components)

#### Installation Steps

git clone https://github.com/ArogueModder/AnyDistro-Disk-Imager.git
cd AnyDistro-Disk-Imager
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e .
anydistro-disk-imager

## Usage

### Graphical Interface

After launching the application, the GUI will guide you through:

1. **Select Image** - Create or choose your `.iso` or `.img` file
2. **Select Target** - Pick your drive or SD card (be careful—data will be overwritten)
3. **Verify** - Verify checksums of disks or images
4. **Write** - Begin writing the image to disk
5. **Read** - Begin reading the disk image to file
6. **Clone** - Clone a disk to a disk of equal or greater size


## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| PyGObject | ≥3.42.0 | GTK 3 bindings for the GUI |
| playsound | ≥1.2.2 | Audio feedback notifications |
| GTK | 3.0+ | Widget toolkit for the interface |

## Building an AppImage

To build your own AppImage for distribution:

pip install -r requirements.txt
./build-appimage.sh

The resulting AppImage will be saved to `dist/AnyDistro_Disk_Imager-x86_64.AppImage`.

### Build Requirements

- Python 3.8+
- linuxdeploy and linuxdeploy-plugin-appimage
- All runtime dependencies (see `requirements.txt`)

## System Requirements

- **OS:** Linux (any distribution)
- **Architecture:** x86_64 (or native for your platform)
- **RAM:** Minimal (50 MB for operation)
- **Storage:** ~50 MB for installation

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

# Debian/Ubuntu
sudo apt install libgtk-3-0 gir1.2-gtk-3.0

# Fedora/RHEL
sudo dnf install gtk3

# Arch
sudo pacman -S gtk3

## License

This project is licensed under the GNU General Public License v3.0 — see the LICENSE file for details.

## Changelog

### Version 1.0.0
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

