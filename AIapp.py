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

    def __init__(self):
        # Page 1: Read/Write
        self.selected_disk: str = ""
        self.image_path: str = ""
        self.block_size: str = "4M"
        self.disable_automount: bool = False
        self.unmount_and_remount: bool = False
        
        # Page 2: Verify
        self.verify_disk: str = ""
        self.verify_image_path: str = ""
        self.hash_algorithm_disk: str = "SHA256"
        self.hash_algorithm_image: str = "SHA256"
        
        # Page 3: Clone
        self.clone_source: str = ""
        self.clone_target: str = ""
        self.clone_block_size: str = "4M"
        self.clone_disable_automount: bool = False
        self.clone_unmount_and_remount: bool = False

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
        # Load Glade file
        self.builder = Gtk.Builder()
        glade_path = Path(__file__).parent / "DiskImager.glade"
        self.builder.add_from_file(str(glade_path))

        
        # Get main window
        self.window = self.builder.get_object("MyMainWindow")
        if not self.window:
            raise RuntimeError("Failed to load main window from Glade file")
        
        # Cache common widgets for quick access
        self.progress_bar = self.builder.get_object("imageProgressBar")
        self.status_label = self.builder.get_object("diskImageProgressPercentageLabel")
        self.image_entry = self.builder.get_object("imageFileText")
        
        # Initialize state and managers
        self.state = AppState()
        self.disk_manager = DiskManager()
        self.mount_manager = MountManager()
        
        # Operation flags
        self.is_operating = False
        self.operation_stopped = False
        self.setup_ui()
        # Connect all signals
        self.connect_signals()

                # Initialize diskSelectCombo with proper model and renderers
        disk_combo = self.builder.get_object("diskSelectCombo")
        if disk_combo:
            # Create ListStore for disk selection
            disk_liststore = Gtk.ListStore(str, str, str)  # name, size_fs, size_total
            disk_combo.set_model(disk_liststore)
            
            # Add first column (disk name)
            renderer_name = Gtk.CellRendererText()
            renderer_name.props.alignment = Pango.Alignment.LEFT
            disk_combo.pack_start(renderer_name, True)
            disk_combo.add_attribute(renderer_name, "text", 0)
            
            # Add second column (filesystem size)
            renderer_size_fs = Gtk.CellRendererText()
            renderer_size_fs.props.alignment = Pango.Alignment.LEFT
            disk_combo.pack_start(renderer_size_fs, True)
            disk_combo.add_attribute(renderer_size_fs, "text", 1)
            
            # Add third column (total disk size)
            renderer_size_total = Gtk.CellRendererText()
            renderer_size_total.props.alignment = Pango.Alignment.LEFT
            disk_combo.pack_start(renderer_size_total, True)
            disk_combo.add_attribute(renderer_size_total, "text", 2)
            
            # Prepend "Select Disk" option
            disk_liststore.append(["Select Disk", " File System Size", "Total Disk Size"])
                
        # Load initial disk list
        self.on_refresh_disks(None)
        
        # Set window icon if available
        icon_path = Path(__file__).parent / "DiskImager.ico"
        if icon_path.exists():
            try:
                self.window.set_icon_from_file(str(icon_path))
            except:
                pass
        
        # Connect window destroy signal
        self.window.connect("destroy", self.on_window_destroy)
        
        # Show window
        self.window.show_all()


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
        """Manually connect all signals from Glade objects to handler methods."""
        
        # PAGE 1: READ/WRITE Tab
        self.connect_button("readImageButton", self.on_read_clicked)
        self.connect_button("writeImageButton", self.on_write_clicked)
        self.connect_button("refreshDiskButton", self.on_refresh_disks)
        self.connect_button("quitButton", self.on_window_destroy)
        self.connect_button("openSaveImageButton", self.on_browse_clicked)
        
        # Combo boxes and entries
        self.connect_combo("diskSelectCombo", self.on_disk_selected)
        self.connect_combo("selectblocksizeCombo", self.on_block_size_changed)
        self.connect_checkbutton("toggleDisableAutomount", self.on_automount_toggled)
        self.connect_checkbutton("toggleUnmountandRemount", self.on_remount_toggled)
        self.connect_entry("imageFileText", self.on_image_path_changed)
        
        # Dialog buttons - Read Image Dialog
        self.connect_button("DialogButtonOK", self.on_read_dialog_ok)
        self.connect_button("DialogButtonCancel", self.on_read_dialog_cancel)
        
        # Dialog buttons - Write Image Dialog
        self.connect_button("DialogButtonOK1", self.on_write_dialog_ok)
        self.connect_button("DialogButtonCancel1", self.on_write_dialog_cancel)
        
        # General Warning Dialog
        self.connect_button("GeneralWarningButton", self.on_general_warning_close)
        
        # PAGE 2: VERIFY Tab
        self.connect_button("generateChecksumDiskButton", self.on_generate_checksum_disk)
        self.connect_button("generateChecksumImageButton", self.on_generate_checksum_image)
        self.connect_button("refreshDiskButton1", self.on_refresh_disks_verify)
        self.connect_button("openimageButton2", self.on_browse_verify_image)
        
        self.connect_combo("verifydiskcombobox", self.on_verify_disk_selected)
        self.connect_combo("checksumCombobox1", self.on_hash_type_disk_changed)
        self.connect_combo("checksumCombobox2", self.on_hash_type_image_changed)
        self.connect_entry("imageverifyentry", self.on_verify_image_path_changed)
        
        # PAGE 3: CLONE Tab
        self.connect_button("CloneDiskButton", self.on_clone_disk_clicked)
        self.connect_button("QuitButton1", self.on_window_destroy)
        self.connect_button("refreshDiskButton2", self.on_refresh_disks_clone)
        
        self.connect_combo("clonediskcombobox1", self.on_clone_source_selected)
        self.connect_combo("clonediskcombobox2", self.on_clone_target_selected)
        self.connect_combo("selectblocksizeCombo1", self.on_clone_block_size_changed)
        self.connect_checkbutton("toggleDisableAutomount1", self.on_clone_automount_toggled)
        self.connect_checkbutton("toggleUnmountandRemount1", self.on_clone_remount_toggled)
        
        # Clone Dialog buttons
        self.connect_button("DialogButtonOK3", self.on_clone_dialog_ok)
        self.connect_button("DialogButtonCancel3", self.on_clone_dialog_cancel)

    def connect_button(self, button_id: str, handler: Callable) -> None:
        """Helper to safely connect button signals."""
        button = self.builder.get_object(button_id)
        if button:
            button.connect("clicked", handler)
            logger.debug(f"Connected button: {button_id}")
        else:
            logger.debug(f"Button not found: {button_id}")

    def connect_combo(self, combo_id: str, handler: Callable) -> None:
        """Helper to safely connect combo box signals."""
        combo = self.builder.get_object(combo_id)
        if combo:
            combo.connect("changed", handler)
            logger.debug(f"Connected combo: {combo_id}")
        else:
            logger.debug(f"Combo not found: {combo_id}")

    def connect_checkbutton(self, button_id: str, handler: Callable) -> None:
        """Helper to safely connect checkbutton signals."""
        button = self.builder.get_object(button_id)
        if button:
            button.connect("toggled", handler)
            logger.debug(f"Connected checkbutton: {button_id}")
        else:
            logger.debug(f"Checkbutton not found: {button_id}")

    def connect_entry(self, entry_id: str, handler: Callable) -> None:
        """Helper to safely connect entry signals."""
        entry = self.builder.get_object(entry_id)
        if entry:
            entry.connect("changed", handler)
            logger.debug(f"Connected entry: {entry_id}")
        else:
            logger.debug(f"Entry not found: {entry_id}")




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

    def on_refresh_disks(self, widget):
        """Handler for refreshing disk list in read/write tab."""
        logger.info("Refresh disks button clicked")
        disk_combo = self.builder.get_object("diskSelectCombo")
        if disk_combo is None:
            logger.error("diskSelectCombo not found")
            return
        
        model = disk_combo.get_model()
        if model is None:
            logger.error("diskSelectCombo has no model - initialization failed")
            return
        
        # Clear existing entries (but keep the "Select Disk" header)
        model.clear()
        model.append(["Select Disk", " File System Size", "Total Disk Size"])
        
        try:
            disks = self.disk_manager.discover_disks()
            logger.info(f"Discovered {len(disks)} disks")
            for disk in disks:
                logger.debug(f"Adding disk: {disk.name} ({disk.size_human})")
                model.append([disk.name, "", disk.size_human])
            
            disk_combo.set_active(0)  # Select "Select Disk" by default
        except Exception as e:
            logger.error(f"Error refreshing disks: {e}")


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

# ===================== PAGE 1: READ/WRITE HANDLERS =====================

    def on_disk_selected(self, widget) -> None:
        """Handle disk selection from combo box."""
        active_iter = widget.get_active_iter()
        if active_iter:
            model = widget.get_model()
            self.state.selected_disk = model[active_iter][0]
            logger.info(f"Disk selected: {self.state.selected_disk}")

    def on_image_path_changed(self, widget) -> None:
        """Handle image file path entry."""
        self.state.image_path = widget.get_text()
        logger.debug(f"Image path: {self.state.image_path}")

    def on_block_size_changed(self, widget) -> None:
        """Handle block size combo change (Page 1)."""
        active_text = widget.get_active_text()
        if active_text:
            self.state.block_size = active_text
            logger.debug(f"Block size changed to: {self.state.block_size}")

    def on_automount_toggled(self, widget) -> None:
        """Handle automount checkbox toggle (Page 1)."""
        self.state.disable_automount = widget.get_active()
        logger.debug(f"Automount disabled: {self.state.disable_automount}")

    def on_remount_toggled(self, widget) -> None:
        """Handle remount checkbox toggle (Page 1)."""
        self.state.unmount_and_remount = widget.get_active()
        logger.debug(f"Unmount and remount: {self.state.unmount_and_remount}")

    def on_read_clicked(self, widget) -> None:
        """Handle read disk to image button."""
        if not self.state.selected_disk or not self.state.image_path:
            self._show_error("Please select a disk and specify an image file path")
            return
        
        # Show confirmation dialog
        read_dialog = self.builder.get_object("ReadDialogBox")
        read_label = self.builder.get_object("ReadDialogMessageLabel")
        
        read_label.set_text(
            f"You are about to read disk {self.state.selected_disk} "
            f"to image file:\n\n{self.state.image_path}\n\nProceed?"
        )
        read_dialog.set_transient_for(self.window)
        read_dialog.set_modal(True)
        read_dialog.run()

    def on_write_clicked(self, widget) -> None:
        """Handle write image to disk button."""
        if not self.state.selected_disk or not self.state.image_path:
            self._show_error("Please select a disk and specify an image file path")
            return
        
        # Show confirmation dialog
        write_dialog = self.builder.get_object("WriteDialogBox")
        write_label = self.builder.get_object("WriteDialogMessageLabel")
        
        write_label.set_text(
            f"You are about to write image file:\n\n{self.state.image_path} "
            f"\n\nto disk {self.state.selected_disk}\n\nProceed?"
        )
        write_dialog.set_transient_for(self.window)
        write_dialog.set_modal(True)
        write_dialog.run()

    def on_read_dialog_ok(self, widget) -> None:
        """Handle OK button in read dialog."""
        self.builder.get_object("ReadDialogBox").hide()
        self._start_operation(OperationType.READ)

    def on_read_dialog_cancel(self, widget) -> None:
        """Handle Cancel button in read dialog."""
        self.builder.get_object("ReadDialogBox").hide()

    def on_write_dialog_ok(self, widget) -> None:
        """Handle OK button in write dialog."""
        self.builder.get_object("WriteDialogBox").hide()
        self._start_operation(OperationType.WRITE)

    def on_write_dialog_cancel(self, widget) -> None:
        """Handle Cancel button in write dialog."""
        self.builder.get_object("WriteDialogBox").hide()

    def on_general_warning_close(self, widget) -> None:
        """Handle OK button in general warning dialog."""
        self.builder.get_object("GeneralErrorWarning").hide()



# ===================== PAGE 2: VERIFY HANDLERS =====================

    def on_verify_disk_selected(self, widget) -> None:
        """Handle disk selection in verify tab."""
        active_iter = widget.get_active_iter()
        if active_iter:
            model = widget.get_model()
            self.state.verify_disk = model[active_iter][0]
            logger.info(f"Verify disk selected: {self.state.verify_disk}")

    def on_verify_image_path_changed(self, widget) -> None:
        """Handle image path entry in verify tab."""
        self.state.verify_image_path = widget.get_text()
        logger.debug(f"Verify image path: {self.state.verify_image_path}")

    def on_hash_type_disk_changed(self, widget) -> None:
        """Handle hash type selection for disk."""
        active_text = widget.get_active_text()
        if active_text:
            self.state.hash_algorithm_disk = active_text
            logger.debug(f"Disk hash algorithm: {self.state.hash_algorithm_disk}")

    def on_hash_type_image_changed(self, widget) -> None:
        """Handle hash type selection for image."""
        active_text = widget.get_active_text()
        if active_text:
            self.state.hash_algorithm_image = active_text
            logger.debug(f"Image hash algorithm: {self.state.hash_algorithm_image}")

    def on_generate_checksum_disk(self, widget) -> None:
        """Generate checksum for disk."""
        if not self.state.verify_disk:
            self._show_error("Please select a disk")
            return
        
        algorithm = self.state.hash_algorithm_disk or "SHA256"
        logger.info(f"Generating {algorithm} hash for disk {self.state.verify_disk}")
        
        # Run in background thread
        thread = threading.Thread(
            target=self._compute_disk_hash,
            args=(self.state.verify_disk, algorithm),
            daemon=True
        )
        thread.start()

    def on_generate_checksum_image(self, widget) -> None:
        """Generate checksum for image file."""
        if not self.state.verify_image_path or not Path(self.state.verify_image_path).exists():
            self._show_error("Please select a valid image file")
            return
        
        algorithm = self.state.hash_algorithm_image or "SHA256"
        logger.info(f"Generating {algorithm} hash for image {self.state.verify_image_path}")
        
        # Run in background thread
        thread = threading.Thread(
            target=self._compute_file_hash,
            args=(self.state.verify_image_path, algorithm),
            daemon=True
        )
        thread.start()

    def on_refresh_disks_verify(self, widget):
        """Handler for refreshing disk list in verify tab."""
        logger.info("Refresh disks button clicked (verify tab)")
        disk_combo = self.builder.get_object("verifydiskcombobox")
        if disk_combo is None:
            logger.error("verifydiskcombobox not found")
            return
        
        # Get the model (ListStore) from the combo box
        model = disk_combo.get_model()
        if model:
            model.clear()
        else:
            logger.warning("No model found for verifydiskcombobox")
            return
        
        # Refresh disk list
        try:
            disks = self.disk_manager.discover_disks()  # Fixed: discover_disks
            model.append(["Select Disk"])
            for disk in disks:
                model.append([disk.name])  # Fixed: append disk.name
            disk_combo.set_active(0)
            logger.info(f"Verify disk list refreshed: {len(disks)} disks found")
        except Exception as e:
            logger.error(f"Error refreshing disks: {e}")
            self.on_generalWarningError(f"Error refreshing disk list: {e}")

    def on_browse_verify_image(self, widget) -> None:
        """Browse for image file in verify tab."""
        dialog = Gtk.FileChooserDialog(
            "Select Image File",
            self.window,
            Gtk.FileChooserAction.OPEN,
            ("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.ACCEPT)
        )
        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            filename = dialog.get_filename()
            verify_entry = self.builder.get_object("imageverifyentry")
            if verify_entry:
                verify_entry.set_text(filename)
        dialog.destroy()


    def on_generate_checksum_disk(self, widget):
        """Handler for generating checksum of selected disk."""
        logger.info("Generate checksum disk button clicked")
        if not self.app_state.verify_disk:
            self.on_generalWarningError("Please select a disk")
            return
        # Implementation would hash the selected disk
        pass

    def on_generate_checksum_image(self, widget):
        """Handler for generating checksum of image file."""
        logger.info("Generate checksum image button clicked")
        if not self.app_state.verify_image_path:
            self.on_generalWarningError("Please select an image file")
            return
        # Implementation would hash the selected image
        pass

    def on_refresh_disks_verify(self, widget):
        """Handler for refreshing disk list in verify tab."""
        logger.info("Refresh disks button clicked (verify tab)")
        # Implementation would refresh the disk list
        pass

    def on_browse_verify_image(self, widget):
        """Handler for browsing image file in verify tab."""
        logger.info("Browse image button clicked (verify tab)")
        dialog = Gtk.FileChooserDialog(
            "Select Image File",
            self.window,
            Gtk.FileChooserAction.OPEN,
            ("_Cancel", Gtk.ResponseType.CANCEL, "_Open", Gtk.ResponseType.ACCEPT)
        )
        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            filename = dialog.get_filename()
            self.app_state.verify_image_path = filename
            image_entry = self.builder.get_object("imageverifyentry")
            if image_entry:
                image_entry.set_text(filename)
        dialog.destroy()

    def on_verify_disk_selected(self, widget):
        """Handler for disk selection in verify tab."""
        active_iter = widget.get_active_iter()
        if active_iter is not None:
            model = widget.get_model()
            disk_name = model[active_iter][0]
            self.app_state.verify_disk = f"/dev/{disk_name}" if disk_name != "Select Disk" else ""
            logger.info(f"Verify disk selected: {self.app_state.verify_disk}")

    def on_hash_type_disk_changed(self, widget):
        """Handler for hash algorithm selection (disk)."""
        active_iter = widget.get_active_iter()
        if active_iter is not None:
            model = widget.get_model()
            hash_type = model[active_iter][0]
            self.app_state.hash_algorithm_disk = hash_type
            logger.info(f"Hash algorithm (disk) changed to: {hash_type}")

    def on_hash_type_image_changed(self, widget):
        """Handler for hash algorithm selection (image)."""
        active_iter = widget.get_active_iter()
        if active_iter is not None:
            model = widget.get_model()
            hash_type = model[active_iter][0]
            self.app_state.hash_algorithm_image = hash_type
            logger.info(f"Hash algorithm (image) changed to: {hash_type}")

    def on_verify_image_path_changed(self, widget):
        """Handler for image path entry changes in verify tab."""
        self.app_state.verify_image_path = widget.get_text()
        logger.debug(f"Verify image path changed to: {self.app_state.verify_image_path}")


# ===================== PAGE 3: CLONE HANDLERS =====================

    def on_clone_disk_clicked(self, widget) -> None:
        """Handle clone disk button."""
        if not self.state.clone_source or not self.state.clone_target:
            self._show_error("Please select both source and target disks")
            return
        
        if self.state.clone_source == self.state.clone_target:
            self._show_error("Source and target disks must be different")
            return
        
        # Show confirmation dialog
        clone_dialog = self.builder.get_object("CloneDiskDialogBox")
        clone_label = self.builder.get_object("CloneDialogMessageLabel")
        
        clone_label.set_text(
            f"Clone from {self.state.clone_source} to {self.state.clone_target}?\n\n"
            f"This will overwrite all data on {self.state.clone_target}!\n\nProceed?"
        )
        clone_dialog.set_transient_for(self.window)
        clone_dialog.set_modal(True)
        clone_dialog.run()

    def on_clone_dialog_ok(self, widget) -> None:
        """Handle OK button in clone dialog."""
        self.builder.get_object("CloneDiskDialogBox").hide()
        self._start_operation(OperationType.CLONE)

    def on_clone_dialog_cancel(self, widget) -> None:
        """Handle Cancel button in clone dialog."""
        self.builder.get_object("CloneDiskDialogBox").hide()

    def on_refresh_disks_clone(self, widget):
        """Handler for refreshing disk list in clone tab."""
        logger.info("Refresh disks button clicked (clone tab)")
        
        # Refresh source disk combo
        source_combo = self.builder.get_object("clonediskcombobox1")
        if source_combo:
            model = source_combo.get_model()
            if model:
                model.clear()
                try:
                    disks = self.disk_manager.discover_disks()  # Fixed: discover_disks
                    model.append(["Select Disk"])
                    for disk in disks:
                        model.append([disk.name])  # Fixed: append disk.name
                    source_combo.set_active(0)
                except Exception as e:
                    logger.error(f"Error refreshing source disks: {e}")
        
        # Refresh target disk combo
        target_combo = self.builder.get_object("clonediskcombobox2")
        if target_combo:
            model = target_combo.get_model()
            if model:
                model.clear()
                try:
                    disks = self.disk_manager.discover_disks()  # Fixed: discover_disks
                    model.append(["Select Disk"])
                    for disk in disks:
                        model.append([disk.name])  # Fixed: append disk.name
                    target_combo.set_active(0)
                except Exception as e:
                    logger.error(f"Error refreshing target disks: {e}")
        
        logger.info("Clone disk lists refreshed")


    def on_refresh_disks(self, widget):
        """Handler for refreshing disk list in read/write tab."""
        logger.info("Refresh disks button clicked")
        disk_combo = self.builder.get_object("diskSelectCombo")
        if disk_combo is None:
            logger.error("diskSelectCombo not found")
            return
        
        # Get the model (ListStore) from the combo box
        model = disk_combo.get_model()
        if model:
            model.clear()
        else:
            logger.warning("No model found for diskSelectCombo")
            return
        
        # Refresh disk list
        try:
            disks = self.disk_manager.discover_disks()  # Fixed: discover_disks, not get_available_disks
            model.append(["Select Disk"])
            for disk in disks:
                model.append([disk.name])  # Fixed: append disk.name, not disk object
            disk_combo.set_active(0)
            logger.info(f"Disk list refreshed: {len(disks)} disks found")
        except Exception as e:
            logger.error(f"Error refreshing disks: {e}")
            self.on_generalWarningError(f"Error refreshing disk list: {e}")
        def on_browse_clicked(self, widget) -> None:
            """Handle browse button for image file selection."""
            dialog = Gtk.FileChooserDialog(
                "Select Image File",
                self.window,
                Gtk.FileChooserAction.SAVE,
                ("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.ACCEPT)
            )
            response = dialog.run()
            if response == Gtk.ResponseType.ACCEPT:
                filename = dialog.get_filename()
                image_entry = self.builder.get_object("imageFileText")
                if image_entry:
                    image_entry.set_text(filename)
            dialog.destroy()

    def on_window_destroy(self, widget) -> None:
        """Handle window close/quit button."""
        if self.is_operating:
            response = self._show_confirmation(
                "Operation in progress. Are you sure you want to quit?"
            )
            if response != Gtk.ResponseType.YES:
                return
            self.operation_stopped = True
        
        logger.info("Application closing")
        Gtk.main_quit()

    def on_stop_clicked(self, widget) -> None:
        """Handle stop button during operation."""
        self.operation_stopped = True
        self.is_operating = False
        self._show_status("Operation stopped by user", False)
        logger.info("Operation stopped by user")

    def on_clone_source_selected(self, widget):
        """Handler for clone source disk selection."""
        active_iter = widget.get_active_iter()
        if active_iter is not None:
            model = widget.get_model()
            disk_name = model[active_iter][0]
            self.app_state.clone_source = f"/dev/{disk_name}" if disk_name != "Select Disk" else ""
            logger.info(f"Clone source disk selected: {self.app_state.clone_source}")

    def on_clone_target_selected(self, widget):
        """Handler for clone target disk selection."""
        active_iter = widget.get_active_iter()
        if active_iter is not None:
            model = widget.get_model()
            disk_name = model[active_iter][0]
            self.app_state.clone_target = f"/dev/{disk_name}" if disk_name != "Select Disk" else ""
            logger.info(f"Clone target disk selected: {self.app_state.clone_target}")

    def on_clone_block_size_changed(self, widget):
        """Handler for block size selection in clone tab."""
        active_iter = widget.get_active_iter()
        if active_iter is not None:
            model = widget.get_model()
            block_size = model[active_iter][0]
            self.app_state.clone_block_size = block_size
            logger.info(f"Clone block size changed to: {block_size}")

    def on_clone_automount_toggled(self, widget):
        """Handler for disable automount toggle in clone tab."""
        self.app_state.clone_disable_automount = widget.get_active()
        status = "disabled" if self.app_state.clone_disable_automount else "enabled"
        logger.info(f"Clone automount {status}")

    def on_clone_remount_toggled(self, widget):
        """Handler for unmount and remount toggle in clone tab."""
        self.app_state.clone_unmount_and_remount = widget.get_active()
        status = "enabled" if self.app_state.clone_unmount_and_remount else "disabled"
        logger.info(f"Clone remount {status}")

    def on_refresh_disks_clone(self, widget):
        """Handler for refreshing disk list in clone tab."""
        logger.info("Refresh disks button clicked (clone tab)")
        # Implementation would refresh the disk list
        pass

    def on_clone_disk_clicked(self, widget):
        """Handler for initiating clone operation."""
        logger.info("Clone disk button clicked")
        if not self.app_state.clone_source or not self.app_state.clone_target:
            self.on_generalWarningError("Please select both source and target disks")
            return
        if self.app_state.clone_source == self.app_state.clone_target:
            self.on_generalWarningError("Source and target disks cannot be the same")
            return
        
        # Show confirmation dialog
        clone_dialog = self.builder.get_object("CloneDiskDialogBox")
        clone_label = self.builder.get_object("CloneDialogMessageLabel")
        if clone_dialog and clone_label:
            clone_label.set_line_wrap(True)
            clone_label.set_line_wrap_mode(Gtk.WrapMode.WORD)
            clone_dialog.set_title("Clone Disk Confirmation")
            clone_label.set_text(
                f"You are about to clone disk:\n\n{self.app_state.clone_source}\n\n"
                f"to:\n\n{self.app_state.clone_target}\n\nWould you like to proceed?"
            )
            clone_dialog.set_transient_for(self.window)
            clone_dialog.set_modal(True)
            clone_dialog.run()

    def on_clone_dialog_ok(self, widget):
        """Handler for confirming clone operation."""
        logger.info("Clone operation confirmed")
        clone_dialog = self.builder.get_object("CloneDiskDialogBox")
        if clone_dialog:
            clone_dialog.hide()
        
        # Perform the clone operation
        if self.app_state.clone_disable_automount:
            # Stop automount services
            logger.info("Stopping automount services for clone operation")
        
        # Start clone thread
        logger.info(f"Starting clone from {self.app_state.clone_source} to {self.app_state.clone_target}")
        # Implementation would perform the actual clone

    def on_clone_dialog_cancel(self, widget):
        """Handler for cancelling clone operation."""
        logger.info("Clone operation cancelled")
        clone_dialog = self.builder.get_object("CloneDiskDialogBox")
        if clone_dialog:
            clone_dialog.hide()


# ===================== HELPER METHODS =====================

    def _start_operation(self, operation_type: str) -> None:
        """Start a disk operation (read, write, or clone) in background thread."""
        if self.is_operating:
            self._show_error("An operation is already in progress")
            return
        
        self.is_operating = True
        self.operation_stopped = False
        
        # Disable automount if requested
        if self.state.disable_automount:
            self.mount_manager.disable_automount()
        
        # Start operation in background thread
        if operation_type == OperationType.READ:
            thread = threading.Thread(
                target=self._execute_read_operation,
                daemon=True
            )
        elif operation_type == OperationType.WRITE:
            thread = threading.Thread(
                target=self._execute_write_operation,
                daemon=True
            )
        elif operation_type == OperationType.CLONE:
            thread = threading.Thread(
                target=self._execute_clone_operation,
                daemon=True
            )
        else:
            return
        
        thread.start()

    def _execute_read_operation(self) -> None:
        """Execute read operation (disk to image)."""
        try:
            logger.info(f"Starting read operation: {self.state.selected_disk} -> {self.state.image_path}")
            
            disk_handler = DiskOperationHandler(
                source=self.state.selected_disk,
                destination=self.state.image_path,
                block_size=self.state.block_size,
                operation_type=OperationType.READ,
                progress_callback=self._update_progress
            )
            
            disk_handler.execute()
            
            if not self.operation_stopped:
                GLib.idle_add(
                    lambda: self._show_status(
                        f"Successfully read {self.state.selected_disk} to {self.state.image_path}",
                        True
                    )
                )
            
            logger.info("Read operation completed successfully")
        
        except Exception as e:
            logger.error(f"Read operation failed: {e}")
            GLib.idle_add(lambda: self._show_error(f"Read operation failed: {e}"))
        
        finally:
            self.is_operating = False
            if self.state.unmount_and_remount:
                self.mount_manager.remount_disk(self.state.selected_disk)
            if self.state.disable_automount:
                self.mount_manager.enable_automount()

    def _execute_write_operation(self) -> None:
        """Execute write operation (image to disk)."""
        try:
            logger.info(f"Starting write operation: {self.state.image_path} -> {self.state.selected_disk}")
            
            disk_handler = DiskOperationHandler(
                source=self.state.image_path,
                destination=self.state.selected_disk,
                block_size=self.state.block_size,
                operation_type=OperationType.WRITE,
                progress_callback=self._update_progress
            )
            
            disk_handler.execute()
            
            if not self.operation_stopped:
                GLib.idle_add(
                    lambda: self._show_status(
                        f"Successfully wrote {self.state.image_path} to {self.state.selected_disk}",
                        True
                    )
                )
            
            logger.info("Write operation completed successfully")
        
        except Exception as e:
            logger.error(f"Write operation failed: {e}")
            GLib.idle_add(lambda: self._show_error(f"Write operation failed: {e}"))
        
        finally:
            self.is_operating = False
            if self.state.unmount_and_remount:
                self.mount_manager.remount_disk(self.state.selected_disk)
            if self.state.disable_automount:
                self.mount_manager.enable_automount()

    def _execute_clone_operation(self) -> None:
        """Execute clone operation (disk to disk)."""
        try:
            logger.info(f"Starting clone operation: {self.state.clone_source} -> {self.state.clone_target}")
            
            disk_handler = DiskOperationHandler(
                source=self.state.clone_source,
                destination=self.state.clone_target,
                block_size=self.state.clone_block_size,
                operation_type=OperationType.CLONE,
                progress_callback=self._update_progress
            )
            
            disk_handler.execute()
            
            if not self.operation_stopped:
                GLib.idle_add(
                    lambda: self._show_status(
                        f"Successfully cloned {self.state.clone_source} to {self.state.clone_target}",
                        True
                    )
                )
            
            logger.info("Clone operation completed successfully")
        
        except Exception as e:
            logger.error(f"Clone operation failed: {e}")
            GLib.idle_add(lambda: self._show_error(f"Clone operation failed: {e}"))
        
        finally:
            self.is_operating = False
            if self.state.clone_disable_automount:
                self.mount_manager.enable_automount()

    def _compute_disk_hash(self, disk_path: str, algorithm: str) -> None:
        """Compute hash of entire disk in background thread."""
        try:
            logger.info(f"Computing {algorithm} hash for {disk_path}")
            
            hasher = hashlib.new(algorithm.lower())
            total_size = DiskManager.get_disk_size(disk_path)
            bytes_read = 0
            
            with open(disk_path, 'rb') as f:
                while True:
                    if self.operation_stopped:
                        break
                    
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    
                    hasher.update(chunk)
                    bytes_read += len(chunk)
                    
                    GLib.idle_add(
                        lambda: self._update_verify_progress(
                            bytes_read, total_size, f"Computing {algorithm}"
                        )
                    )
            
            hash_value = hasher.hexdigest()
            
            GLib.idle_add(
                lambda: self._show_hash_result(
                    f"{algorithm} Hash of {disk_path}:\n\n{hash_value}",
                    algorithm
                )
            )
            
            logger.info(f"{algorithm} hash computed: {hash_value}")
        
        except Exception as e:
            logger.error(f"Hash computation failed: {e}")
            GLib.idle_add(lambda: self._show_error(f"Hash computation failed: {e}"))

    def _compute_file_hash(self, file_path: str, algorithm: str) -> None:
        """Compute hash of image file in background thread."""
        try:
            logger.info(f"Computing {algorithm} hash for {file_path}")
            
            hasher = hashlib.new(algorithm.lower())
            total_size = Path(file_path).stat().st_size
            bytes_read = 0
            
            with open(file_path, 'rb') as f:
                while True:
                    if self.operation_stopped:
                        break
                    
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    
                    hasher.update(chunk)
                    bytes_read += len(chunk)
                    
                    GLib.idle_add(
                        lambda: self._update_verify_progress(
                            bytes_read, total_size, f"Computing {algorithm}"
                        )
                    )
            
            hash_value = hasher.hexdigest()
            
            GLib.idle_add(
                lambda: self._show_hash_result(
                    f"{algorithm} Hash of {file_path}:\n\n{hash_value}",
                    algorithm
                )
            )
            
            logger.info(f"{algorithm} hash computed: {hash_value}")
        
        except Exception as e:
            logger.error(f"Hash computation failed: {e}")
            GLib.idle_add(lambda: self._show_error(f"Hash computation failed: {e}"))

    def _show_hash_result(self, message: str, algorithm: str) -> None:
        """Display hash result in a dialog."""
        dialog = Gtk.MessageDialog(
            parent=self.window,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            message_format=message
        )
        dialog.run()
        dialog.destroy()

    def _show_confirmation(self, message: str) -> int:
        """Show a yes/no confirmation dialog."""
        dialog = Gtk.MessageDialog(
            parent=self.window,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            message_format=message
        )
        response = dialog.run()
        dialog.destroy()
        return response

    def _update_progress(self, bytes_done: int, total_bytes: int, status: str) -> None:
        """Update progress bar and status label."""
        if total_bytes > 0:
            fraction = bytes_done / total_bytes
            progress_bar = self.builder.get_object("imageProgressBar")
            if progress_bar:
                GLib.idle_add(lambda: progress_bar.set_fraction(fraction))
        
        percentage = int((bytes_done / total_bytes) * 100) if total_bytes > 0 else 0
        status_label = self.builder.get_object("diskImageProgressPercentageLabel")
        if status_label:
            GLib.idle_add(lambda: status_label.set_text(f"{status} {percentage}%"))

    def _update_verify_progress(self, bytes_done: int, total_bytes: int, status: str) -> None:
        """Update verification progress."""
        self._update_progress(bytes_done, total_bytes, status)

    def _show_status(self, message: str, success: bool) -> None:
        """Display status message."""
        dialog_type = Gtk.MessageType.INFO if success else Gtk.MessageType.ERROR
        dialog = Gtk.MessageDialog(
            parent=self.window,
            flags=Gtk.DialogFlags.MODAL,
            type=dialog_type,
            buttons=Gtk.ButtonsType.OK,
            message_format=message
        )
        dialog.run()
        dialog.destroy()
        logger.info(f"Operation result: {message}")

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
        logger.error(f"Error: {message}")

    def on_generalWarningError(self, warnError):
        """Display a general warning/error dialog."""
        general_dialog = self.builder.get_object("GeneralErrorWarning")
        general_label = self.builder.get_object("GeneralWarningLabel")
        
        if general_dialog and general_label:
            general_label.set_line_wrap(True)
            general_label.set_line_wrap_mode(Gtk.WrapMode.WORD)
            general_dialog.set_title("Warning")
            general_label.set_text(str(warnError))
            general_dialog.set_transient_for(self.window)
            general_dialog.set_modal(True)
            general_dialog.run()
        else:
            logger.error(f"Warning dialog not found. Error message: {warnError}")

    def on_general_warning_close(self, widget):
        """Close the general warning dialog."""
        general_dialog = self.builder.get_object("GeneralErrorWarning")
        if general_dialog:
            general_dialog.hide()
        logger.debug("Warning dialog closed")





# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================
if __name__ == "__main__":
    app = DiskImagerApp()
    app.run()


