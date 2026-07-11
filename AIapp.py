#!/usr/bin/env python3
"""
AnyDistro Disk Imager - A GTK3-based disk imaging utility for Linux.
Features: Read/Write disk images, verify operations, clone disks.
Refactored version with improved code structure and error handling.
"""
import gi
import subprocess
import time
import os
import sys
import threading
import fcntl
import signal
import re
import shutil
import logging
import logging.handlers
import errno
import json
import warnings
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Callable
from enum import Enum
import hashlib

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango, Gdk, GLib, GObject

try:
    from playsound import playsound
except ImportError:
    def playsound(sound_file):
        pass

# ============================================================================
# CONFIGURATION & CONSTANTS
# ============================================================================
CHUNK_SIZE = 8192
BLOCK_SIZES = ["4M", "1M", "512K", "256K", "64K", "1K"]
HASH_ALGORITHMS = ["MD5", "SHA1", "SHA256", "SHA512"]
AUTOMOUNT_SERVICES = ["autofs", "automount", "udisks2", "gvfs-daemon", "gvfs-metadata"]
AUTOMOUNT_PROCS = ["udiskie", "gvfs", "udisksd", "udisks2", "autofs", "automount"]
GLADE_FILE = "DiskImager.glade"
ICON_FILE = "DiskImager.ico"
LOG_FILE = "disk_imager.log"

# Setup logging
logger = logging.getLogger("DiskImager")
logger.setLevel(logging.DEBUG)
log_dir = Path.home() / ".config" / "disk-imager"
log_dir.mkdir(parents=True, exist_ok=True)
log_path = log_dir / LOG_FILE
handler = logging.handlers.RotatingFileHandler(
    str(log_path), maxBytes=10*1024*1024, backupCount=5
)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
handler.setFormatter(formatter)
logger.addHandler(handler)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# ============================================================================
# ENUMS & DATA CLASSES
# ============================================================================
class OperationType(Enum):
    """Types of disk operations."""
    READ = "read"
    WRITE = "write"
    CLONE = "clone"
    VERIFY = "verify"

@dataclass
class PartitionInfo:
    """Information about a disk partition."""
    path: str
    mountpoint: Optional[str] = None
    kname: Optional[str] = None

    def is_mounted(self) -> bool:
        """Check if partition is currently mounted."""
        return self.mountpoint is not None

@dataclass
class DiskInfo:
    """Information about a disk device."""
    name: str
    path: str
    size_bytes: int
    partitions: List[PartitionInfo] = field(default_factory=list)

    @property
    def size_human(self) -> str:
        """Return human-readable size."""
        return self._format_bytes(self.size_bytes)

    @staticmethod
    def _format_bytes(n: float, base: int = 1000) -> str:
        """Format bytes to human-readable format."""
        units = ['B', 'KB', 'MB', 'GB', 'TB', 'PiB']
        n = float(n)
        for unit in units:
            if abs(n) < base or unit == units[-1]:
                return f"{n:.1f}{unit}"
            n /= base
        return f"{n:.1f}{units[-1]}"

@dataclass
class AppState:
    """Central state management for the application."""
    selected_disk: Optional[str] = None
    selected_disk_size: int = 0
    selected_image_file: Optional[str] = None
    block_size: str = "4M"
    disable_automount: bool = False
    unmount_and_remount: bool = False
    verify_selected_disk: Optional[str] = None
    verify_selected_image: Optional[str] = None
    verify_hash_algorithm: str = "SHA256"
    verify_hash_output: str = ""
    verify_has_run: bool = False
    clone_source_disk: Optional[str] = None
    clone_target_disk: Optional[str] = None
    clone_block_size: str = "4M"
    clone_disable_automount: bool = False
    clone_unmount_and_remount: bool = False
    current_process: Optional[subprocess.Popen] = None
    process_buffer: str = ""
    stopped_services: List[str] = field(default_factory=list)
    killed_pids: List[str] = field(default_factory=list)
    mounted_partitions: List[PartitionInfo] = field(default_factory=list)
    operation_cancelled: bool = False
    readonly_mode: bool = False

    def reset_process_state(self) -> None:
        """Reset process-related state."""
        self.current_process = None
        self.process_buffer = ""
        self.operation_cancelled = False

    def reset_mount_state(self) -> None:
        """Reset mount-related state."""
        self.stopped_services = []
        self.killed_pids = []
        self.mounted_partitions = []

    def validate_disk_path(self, disk_path: str) -> bool:
        """Validate that disk path is safe and exists."""
        if not disk_path or not disk_path.startswith("/dev/"):
            return False
        if not os.path.exists(disk_path):
            return False
        if ".." in disk_path or " " in disk_path:
            return False
        return True

    def validate_file_path(self, file_path: str) -> bool:
        """Validate that file path is safe."""
        if not file_path:
            return False
        if not os.path.isabs(file_path):
            return False
        if ".." in file_path:
            return False
        return True

# ============================================================================
# DISK UTILITIES
# ============================================================================
class DiskManager:
    """Handles disk discovery and operations."""

    @staticmethod
    def discover_disks() -> List[DiskInfo]:
        """Discover all block devices on the system."""
        try:
            result = subprocess.run(
                ["lsblk", "-d", "-J", "-o", "NAME,PATH,SIZE,TYPE"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False
            )
            if result.returncode != 0:
                logger.error(f"lsblk failed: {result.stderr}")
                return []

            data = json.loads(result.stdout)
            disks = []
            for device in data.get("blockdevices", []):
                if device.get("type") not in ["disk", "loop", "nvme", "mmc"]:
                    continue
                if device.get("name", "").startswith(("loop", "dm-")):
                    continue

                try:
                    size_str = device.get("size", "0")
                    size_bytes = DiskManager._parse_size(size_str)
                    disk = DiskInfo(
                        name=device["name"],
                        path=device["path"],
                        size_bytes=size_bytes
                    )
                    disks.append(disk)
                    logger.debug(f"Discovered disk: {disk.name} ({disk.size_human})")
                except Exception as e:
                    logger.warning(f"Failed to parse disk {device.get('name')}: {e}")
            return disks
        except subprocess.TimeoutExpired:
            logger.error("lsblk command timed out")
            return []
        except Exception as e:
            logger.error(f"Error discovering disks: {e}")
            return []

    @staticmethod
    def _parse_size(size_str: str) -> int:
        """Parse size string (e.g., '1G', '512M') to bytes."""
        size_str = size_str.strip().upper()
        units = {
            'B': 1,
            'K': 1024,
            'M': 1024**2,
            'G': 1024**3,
            'T': 1024**4,
        }
        try:
            return int(size_str)
        except ValueError:
            pass

        for unit, multiplier in units.items():
            if size_str.endswith(unit):
                try:
                    number = float(size_str[:-len(unit)])
                    return int(number * multiplier)
                except ValueError:
                    pass
        return 0

    @staticmethod
    def get_disk_size_bytes(disk_name: str) -> int:
        """Get actual disk size in bytes."""
        try:
            name = Path(disk_name).name
            size_path = Path("/sys/block") / name / "size"
            if not size_path.exists():
                raise FileNotFoundError(f"Size file not found for {disk_name}")
            sectors = int(size_path.read_text().strip())
            size_bytes = sectors * 512
            logger.debug(f"Disk {disk_name} size: {size_bytes} bytes")
            return size_bytes
        except Exception as e:
            logger.error(f"Error getting disk size for {disk_name}: {e}")
            return 0

    @staticmethod
    def discover_partitions(disk_path: str) -> List[PartitionInfo]:
        """Discover partitions on a disk."""
        try:
            result = subprocess.run(
                ["lsblk", "-J", "-o", "NAME,KNAME,PATH,MOUNTPOINT,TYPE"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False
            )
            if result.returncode != 0:
                logger.error(f"lsblk failed: {result.stderr}")
                return []

            data = json.loads(result.stdout)
            partitions = []
            for node in data.get("blockdevices", []):
                if Path(node.get("path", "")) == Path(disk_path):
                    for child in node.get("children", []) or []:
                        if child.get("type") == "part":
                            partition = PartitionInfo(
                                path=child.get("path", ""),
                                mountpoint=child.get("mountpoint"),
                                kname=child.get("kname")
                            )
                            partitions.append(partition)
                            logger.debug(f"Found partition: {partition.path}")
            return partitions
        except subprocess.TimeoutExpired:
            logger.error("lsblk command timed out")
            return []
        except Exception as e:
            logger.error(f"Error discovering partitions: {e}")
            return []

# ============================================================================
# MOUNT UTILITIES
# ============================================================================
class MountManager:
    """Handles mounting/unmounting and automount service management."""

    @staticmethod
    def unmount_partitions(partitions: List[PartitionInfo]) -> bool:
        """Unmount all partitions."""
        success = True
        for partition in partitions:
            if not partition.is_mounted():
                continue
            try:
                result = subprocess.run(
                    ["umount", partition.path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False
                )
                if result.returncode == 0:
                    logger.info(f"Unmounted {partition.path}")
                else:
                    logger.warning(f"Failed to unmount {partition.path}: {result.stderr}")
                    success = False
            except Exception as e:
                logger.error(f"Error unmounting {partition.path}: {e}")
                success = False
        return success

    @staticmethod
    def force_unmount_partitions(partitions: List[PartitionInfo]) -> bool:
        """Force unmount partitions using lazy unmount."""
        success = True
        for partition in partitions:
            try:
                result = subprocess.run(
                    ["umount", "-l", partition.path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False
                )
                if result.returncode == 0:
                    logger.info(f"Lazy unmounted {partition.path}")
                else:
                    logger.warning(f"Failed to lazy unmount {partition.path}: {result.stderr}")
                    success = False
            except Exception as e:
                logger.error(f"Error lazy unmounting {partition.path}: {e}")
                success = False
        return success

    @staticmethod
    def remount_partitions(partitions: List[PartitionInfo]) -> bool:
        """Remount partitions to their original locations."""
        success = True
        for partition in partitions:
            if not partition.mountpoint:
                continue
            try:
                Path(partition.mountpoint).mkdir(parents=True, exist_ok=True)
                result = subprocess.run(
                    ["mount", partition.path, partition.mountpoint],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False
                )
                if result.returncode == 0:
                    logger.info(f"Remounted {partition.path} to {partition.mountpoint}")
                else:
                    logger.warning(f"Failed to remount {partition.path}: {result.stderr}")
                    success = False
            except Exception as e:
                logger.error(f"Error remounting {partition.path}: {e}")
                success = False
        return success

    @staticmethod
    def stop_automount_services() -> Tuple[List[str], bool]:
        """Stop automount services before disk operations."""
        stopped_services = []
        success = True
        for service in AUTOMOUNT_SERVICES:
            try:
                result = subprocess.run(
                    ["sudo", "systemctl", "stop", service],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False
                )
                if result.returncode == 0:
                    stopped_services.append(service)
                    logger.info(f"Stopped service: {service}")
                else:
                    logger.debug(f"Service {service} not running or couldn't stop it")
            except Exception as e:
                logger.warning(f"Error stopping service {service}: {e}")
                success = False
        return stopped_services, success

    @staticmethod
    def start_automount_services(services: List[str]) -> bool:
        """Restart previously stopped automount services."""
        success = True
        for service in services:
            try:
                result = subprocess.run(
                    ["sudo", "systemctl", "start", service],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False
                )
                if result.returncode == 0:
                    logger.info(f"Started service: {service}")
                else:
                    logger.warning(f"Failed to start service {service}: {result.stderr}")
                    success = False
            except Exception as e:
                logger.error(f"Error starting service {service}: {e}")
                success = False
        return success

    @staticmethod
    def kill_automount_processes() -> Tuple[List[str], bool]:
        """Kill automount-related processes."""
        killed_pids = []
        success = True
        for proc in AUTOMOUNT_PROCS:
            try:
                result = subprocess.run(
                    ["pgrep", "-f", proc],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False
                )
                if result.returncode == 0:
                    pids = result.stdout.strip().split('\n')
                    for pid in pids:
                        if pid:
                            try:
                                os.kill(int(pid), signal.SIGTERM)
                                killed_pids.append(pid)
                                logger.info(f"Killed process {proc} (PID: {pid})")
                            except Exception as e:
                                logger.warning(f"Failed to kill PID {pid}: {e}")
                                success = False
            except Exception as e:
                logger.debug(f"Process {proc} not found or error: {e}")
                success = False
        return killed_pids, success

# ============================================================================
# DISK OPERATION HANDLERS
# ============================================================================
class DiskOperationHandler:
    """Handles disk read/write/clone operations."""

    @staticmethod
    def read_disk_to_image(
        disk_path: str,
        image_path: str,
        block_size: str = "4M",
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Tuple[bool, str]:
        """Read disk to image file."""
        try:
            disk_size = DiskManager.get_disk_size_bytes(disk_path)
            if disk_size <= 0:
                return False, "Could not determine disk size"

            cmd = [
                "sudo", "dd",
                f"if={disk_path}",
                f"of={image_path}",
                f"bs={block_size}",
                "status=progress"
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            bytes_read = 0
            for line in process.stderr:
                if cancel_event and cancel_event.is_set():
                    process.terminate()
                    return False, "Operation cancelled by user"

                try:
                    bytes_read = int(line.split()[0])
                    if progress_callback:
                        progress_callback(bytes_read, disk_size)
                except (ValueError, IndexError):
                    continue

            process.wait()
            if process.returncode == 0:
                logger.info(f"Successfully read {disk_path} to {image_path}")
                return True, "Read successful"
            else:
                return False, "dd command failed"

        except Exception as e:
            logger.error(f"Error reading disk: {e}")
            return False, str(e)

    @staticmethod
    def write_image_to_disk(
        image_path: str,
        disk_path: str,
        block_size: str = "4M",
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Tuple[bool, str]:
        """Write image to disk."""
        try:
            image_size = os.path.getsize(image_path)
            if image_size <= 0:
                return False, "Image file is empty or not found"

            cmd = [
                "sudo", "dd",
                f"if={image_path}",
                f"of={disk_path}",
                f"bs={block_size}",
                "status=progress"
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            bytes_written = 0
            for line in process.stderr:
                if cancel_event and cancel_event.is_set():
                    process.terminate()
                    return False, "Operation cancelled by user"

                try:
                    bytes_written = int(line.split()[0])
                    if progress_callback:
                        progress_callback(bytes_written, image_size)
                except (ValueError, IndexError):
                    continue

            process.wait()
            subprocess.run(["sudo", "sync"], check=False)
            
            if process.returncode == 0:
                logger.info(f"Successfully wrote {image_path} to {disk_path}")
                return True, "Write successful"
            else:
                return False, "dd command failed"

        except Exception as e:
            logger.error(f"Error writing image: {e}")
            return False, str(e)

    @staticmethod
    def clone_disk(
        source_disk: str,
        target_disk: str,
        block_size: str = "4M",
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Tuple[bool, str]:
        """Clone one disk to another."""
        try:
            source_size = DiskManager.get_disk_size_bytes(source_disk)
            if source_size <= 0:
                return False, "Could not determine source disk size"

            cmd = [
                "sudo", "dd",
                f"if={source_disk}",
                f"of={target_disk}",
                f"bs={block_size}",
                "status=progress"
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            bytes_cloned = 0
            for line in process.stderr:
                if cancel_event and cancel_event.is_set():
                    process.terminate()
                    return False, "Operation cancelled by user"

                try:
                    bytes_cloned = int(line.split()[0])
                    if progress_callback:
                        progress_callback(bytes_cloned, source_size)
                except (ValueError, IndexError):
                    continue

            process.wait()
            subprocess.run(["sudo", "sync"], check=False)
            
            if process.returncode == 0:
                logger.info(f"Successfully cloned {source_disk} to {target_disk}")
                return True, "Clone successful"
            else:
                return False, "dd command failed"

        except Exception as e:
            logger.error(f"Error cloning disk: {e}")
            return False, str(e)

# ============================================================================
# VERIFICATION & HASHING
# ============================================================================
class VerificationHandler:
    """Handles disk verification and hashing operations."""

    @staticmethod
    def hash_file(
        file_path: str,
        algorithm: str = "SHA256",
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Tuple[bool, str]:
        """Compute hash of a file."""
        try:
            hash_obj = hashlib.new(algorithm.lower())
            file_size = os.path.getsize(file_path)
            bytes_read = 0

            with open(file_path, "rb") as f:
                while True:
                    if cancel_event and cancel_event.is_set():
                        return False, "Operation cancelled by user"

                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break

                    hash_obj.update(chunk)
                    bytes_read += len(chunk)

                    if progress_callback:
                        progress_callback(bytes_read, file_size)

            hash_value = hash_obj.hexdigest()
            logger.info(f"Computed {algorithm} hash for {file_path}: {hash_value}")
            return True, hash_value

        except Exception as e:
            logger.error(f"Error hashing file {file_path}: {e}")
            return False, str(e)

    @staticmethod
    def verify_disk_vs_image(
        disk_path: str,
        image_path: str,
        algorithm: str = "SHA256",
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Tuple[bool, str]:
        """Verify that disk matches image by comparing hashes."""
        try:
            # Hash the image
            if progress_callback:
                progress_callback(0, 100, "Hashing image file...")

            success, image_hash = VerificationHandler.hash_file(
                image_path,
                algorithm,
                lambda b, t: progress_callback(int((b/t) * 50), 100, "Hashing image...")
                if progress_callback else None,
                cancel_event
            )

            if not success:
                return False, f"Failed to hash image: {image_hash}"

            # Hash the disk
            if progress_callback:
                progress_callback(50, 100, "Hashing disk...")

            success, disk_hash = VerificationHandler.hash_file(
                disk_path,
                algorithm,
                lambda b, t: progress_callback(50 + int((b/t) * 50), 100, "Hashing disk...")
                if progress_callback else None,
                cancel_event
            )

            if not success:
                return False, f"Failed to hash disk: {disk_hash}"

            # Compare hashes
            if image_hash == disk_hash:
                logger.info(f"Verification successful: hashes match")
                return True, f"Verification successful\n{algorithm}: {image_hash}"
            else:
                logger.warning(f"Verification failed: hashes don't match")
                return False, f"Verification failed\nImage:  {image_hash}\nDisk:   {disk_hash}"

        except Exception as e:
            logger.error(f"Error verifying disk: {e}")
            return False, str(e)

# ============================================================================
# GTK GUI APPLICATION
# ============================================================================
class DiskImagerApp:
    """Main GTK application."""

    def __init__(self):
        """Initialize the application."""
        self.state = AppState()
        self.builder = Gtk.Builder()
        
        # Load Glade file - CORRECTED PATH
        glade_path = Path(__file__).parent / GLADE_FILE
        
        try:
            self.builder.add_from_file(str(glade_path))
        except Exception as e:
            logger.error(f"Failed to load Glade file from {glade_path}: {e}")
            logger.error("Ensure DiskImager.glade is in the same directory as AIapp.py")
            sys.exit(1)
        
        # Get the main window - CORRECT ID
        self.window = self.builder.get_object("MyMainWindow")
        if not self.window:
            logger.error("Main window 'MyMainWindow' not found in Glade file")
            sys.exit(1)
        
        self.window.connect("destroy", self.on_window_destroy)
        
        self.setup_ui()
        self.connect_signals()


def setup_ui(self) -> None:
    """Setup UI components."""
    self.disk_combo = self.builder.get_object("diskSelectCombo")
    self.block_size_combo = self.builder.get_object("selectblocksizeCombo")
    self.image_entry = self.builder.get_object("imageFileText")
    self.progress_bar = self.builder.get_object("imageProgressBar")
    self.status_label = self.builder.get_object("diskImageProgressPercentageLabel")
    self.disable_automount_check = self.builder.get_object("toggleDisableAutomount")
    
    # Verify all objects were found
    required_objects = {
        "diskSelectCombo": self.disk_combo,
        "imageFileText": self.image_entry,
        "selectblocksizeCombo": self.block_size_combo,
        "imageProgressBar": self.progress_bar,
        "toggleDisableAutomount": self.disable_automount_check,
    }
    
    for obj_id, obj in required_objects.items():
        if obj is None:
            logger.error(f"Required object '{obj_id}' not found in Glade file")

def connect_signals(self) -> None:
    """Manually connect signals from Glade objects to handler methods."""
    # Get button objects from Glade and connect them
    buttons_to_connect = {
        "refreshButton": self.on_refresh_disks,
        "readImageButton": self.on_read_clicked,
        "writeImageButton": self.on_write_clicked,
        "cloneDiskButton": self.on_clone_clicked,
        "verifyButton": self.on_verify_clicked,
        "stopButton": self.on_stop_clicked,
        "browseButton": self.on_browse_clicked,
        "quitButton": self.on_window_destroy,
    }
    
    for button_id, handler in buttons_to_connect.items():
        button = self.builder.get_object(button_id)
        if button:
            button.connect("clicked", handler)
        else:
            logger.debug(f"Button '{button_id}' not found in Glade file")
    
    # Connect combo box signals
    if self.block_size_combo:
        self.block_size_combo.connect("changed", self.on_block_size_changed)
    
    # Connect checkbutton signals
    if self.disable_automount_check:
        self.disable_automount_check.connect("toggled", self.on_automount_toggled)
    def refresh_disks(self) -> None:
        """Refresh list of available disks."""
        self.disk_combo.remove_all()
        disks = DiskManager.discover_disks()
        for disk in disks:
            label = f"{disk.name} ({disk.size_human})"
            self.disk_combo.append(disk.path, label)
        if disks:
            self.disk_combo.set_active(0)

    def on_window_destroy(self, widget) -> None:
        """Handle window close."""
        Gtk.main_quit()

    def on_refresh_disks(self, widget) -> None:
        """Handle refresh disks button."""
        self.refresh_disks()

    def on_browse_clicked(self, widget) -> None:
        """Handle browse for image file."""
        dialog = Gtk.FileChooserDialog(
            "Select Image File",
            self.window,
            Gtk.FileChooserAction.SAVE,
            ("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.ACCEPT)
        )
        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            filename = dialog.get_filename()
            self.image_entry.set_text(filename)
        dialog.destroy()

    def on_read_clicked(self, widget) -> None:
        """Handle read disk button."""
        if not self._validate_read_operation():
            return
        self._start_operation(OperationType.READ)

    def on_write_clicked(self, widget) -> None:
        """Handle write image button."""
        if not self._validate_write_operation():
            return
        self._start_operation(OperationType.WRITE)

    def on_clone_clicked(self, widget) -> None:
        """Handle clone disk button."""
        if not self._validate_clone_operation():
            return
        self._start_operation(OperationType.CLONE)

    def on_verify_clicked(self, widget) -> None:
        """Handle verify button."""
        if not self._validate_verify_operation():
            return
        self._start_operation(OperationType.VERIFY)

    def on_stop_clicked(self, widget) -> None:
        """Handle stop button."""
        self.state.operation_cancelled = True


    def on_block_size_changed(self, widget) -> None:
        """Handle block size combo change."""
        active_text = widget.get_active_text()
        if active_text:
            self.state.block_size = active_text
            logger.debug(f"Block size changed to: {self.state.block_size}")

    def on_automount_toggled(self, widget) -> None:
        """Handle automount checkbox toggle."""
        self.state.disable_automount = widget.get_active()
        logger.debug(f"Automount disabled: {self.state.disable_automount}")

    def _show_error(self, message: str) -> None:
        """Display an error dialog."""
        dialog = Gtk.MessageDialog(
            parent=self.window,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            message_format=message
        )
        dialog.run()
        dialog.destroy()


    def _validate_read_operation(self) -> bool:
        """Validate read operation parameters."""
        if not self.state.selected_disk:
            self._show_error("Please select a disk to read")
            return False
        if not self.state.selected_image_file:
            self._show_error("Please select an image file path")
            return False
        return True

    def _validate_write_operation(self) -> bool:
        """Validate write operation parameters."""
        if not self.state.selected_image_file:
            self._show_error("Please select an image file")
            return False
        if not os.path.exists(self.state.selected_image_file):
            self._show_error("Image file does not exist")
            return False
        if not self.state.selected_disk:
            self._show_error("Please select a target disk")
            return False
        return True

    def _validate_clone_operation(self) -> bool:
        """Validate clone operation parameters."""
        if not self.state.clone_source_disk:
            self._show_error("Please select a source disk")
            return False
        if not self.state.clone_target_disk:
            self._show_error("Please select a target disk")
            return False
        if self.state.clone_source_disk == self.state.clone_target_disk:
            self._show_error("Source and target disks must be different")
            return False
        return True

    def _validate_verify_operation(self) -> bool:
        """Validate verify operation parameters."""
        if not self.state.verify_selected_disk:
            self._show_error("Please select a disk to verify")
            return False
        if not self.state.verify_selected_image:
            self._show_error("Please select an image file")
            return False
        if not os.path.exists(self.state.verify_selected_image):
            self._show_error("Image file does not exist")
            return False
        return True

    def _start_operation(self, op_type: OperationType) -> None:
        """Start a disk operation in a background thread."""
        self.state.operation_cancelled = False
        thread = threading.Thread(
            target=self._execute_operation,
            args=(op_type,),
            daemon=True
        )
        thread.start()

    def _execute_operation(self, op_type: OperationType) -> None:
        """Execute disk operation."""
        try:
            partitions = []
            mount_manager = MountManager()

            # Prepare: unmount and disable automount if needed
            if self.state.selected_disk:
                partitions = DiskManager.discover_partitions(self.state.selected_disk)
                if self.state.unmount_and_remount:
                    mount_manager.unmount_partitions(partitions)

            if self.state.disable_automount:
                self.state.stopped_services, _ = mount_manager.stop_automount_services()
                self.state.killed_pids, _ = mount_manager.kill_automount_processes()

            # Set disk to read-only if reading
            if op_type == OperationType.READ and self.state.selected_disk:
                subprocess.run(
                    ["sudo", "blockdev", "--setro"], check=False)

            # Execute operation
            if op_type == OperationType.READ:
                success, message = DiskOperationHandler.read_disk_to_image(
                    self.state.selected_disk,
                    self.state.selected_image_file,
                    self.state.block_size,
                    progress_callback=self._update_progress,
                    cancel_event=threading.Event()
                )

            elif op_type == OperationType.WRITE:
                success, message = DiskOperationHandler.write_image_to_disk(
                    self.state.selected_image_file,
                    self.state.selected_disk,
                    self.state.block_size,
                    progress_callback=self._update_progress,
                    cancel_event=threading.Event()
                )

            elif op_type == OperationType.CLONE:
                success, message = DiskOperationHandler.clone_disk(
                    self.state.clone_source_disk,
                    self.state.clone_target_disk,
                    self.state.clone_block_size,
                    progress_callback=self._update_progress,
                    cancel_event=threading.Event()
                )

            elif op_type == OperationType.VERIFY:
                success, message = VerificationHandler.verify_disk_vs_image(
                    self.state.verify_selected_disk,
                    self.state.verify_selected_image,
                    self.state.verify_hash_algorithm,
                    progress_callback=self._update_verify_progress,
                    cancel_event=threading.Event()
                )

            # Update UI with results
            GLib.idle_add(self._show_status, message, success)

        except Exception as e:
            logger.error(f"Operation failed: {e}")
            GLib.idle_add(self._show_error, str(e))

        finally:
            # Cleanup: remount partitions and restart services
            if partitions and self.state.unmount_and_remount:
                mount_manager.remount_partitions(partitions)

            if self.state.disable_automount:
                mount_manager.start_automount_services(self.state.stopped_services)

            # Set disk back to read-write if it was set to read-only
            if op_type == OperationType.READ and self.state.selected_disk:
                subprocess.run(
                    ["sudo", "blockdev", "--setrw", self.state.selected_disk],
                    check=False
                )

            self.state.reset_process_state()

    def _update_progress(self, current: int, total: int) -> None:
        """Update progress bar during read/write operations."""
        if total <= 0:
            return
        fraction = min(1.0, current / total)
        human_current = DiskInfo._format_bytes(current)
        human_total = DiskInfo._format_bytes(total)
        text = f"{human_current} / {human_total}"
        GLib.idle_add(self.progress_bar.set_fraction, fraction)
        GLib.idle_add(self.progress_bar.set_text, text)

    def _update_verify_progress(self, current: int, total: int, status: str) -> None:
        """Update progress during verify operations."""
        if total <= 0:
            return
        fraction = min(1.0, current / total)
        GLib.idle_add(self.progress_bar.set_fraction, fraction)
        GLib.idle_add(self.progress_bar.set_text, status)

    def _show_status(self, message: str, success: bool) -> None:
        """Show operation status to user."""
        style_context = self.status_label.get_style_context()
        if success:
            style_context.remove_class("error")
            style_context.add_class("success")
        else:
            style_context.remove_class("success")
            style_context.add_class("error")
        self.status_label.set_text(message)

    def _show_error(self, error: str) -> None:
        """Show error dialog."""
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            flags=0,
            type_=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Error"
        )
        dialog.format_secondary_text(error)
        dialog.run()
        dialog.destroy()

    def run(self) -> None:
        """Run the application."""
        self.window.show_all()
        Gtk.main()
    
        # Dialog and checkbox handlers
    def on_block_size_changed(self, widget) -> None:
        """Handle block size combo change."""
        self.state.block_size = widget.get_active_text()
        logger.debug(f"Block size changed to: {self.state.block_size}")

    def on_automount_toggled(self, widget) -> None:
        """Handle automount checkbox toggle."""
        self.state.disable_automount = widget.get_active()
        logger.debug(f"Automount disabled: {self.state.disable_automount}")

    def on_stop_clicked(self, widget) -> None:
        """Handle stop button."""
        self.state.cancel_requested = True
        logger.info("Stop requested by user")

    def on_verify_clicked(self, widget) -> None:
        """Handle verify button."""
        logger.info("Verify operation started")
        # Add verify logic here




# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================
if __name__ == "__main__":
    app = DiskImagerApp()
    app.run()


