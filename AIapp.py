#!/usr/bin/env python3
"""
AnyDistro Disk Imager - A GTK3-based disk imaging utility for Linux.
Features: Read/Write disk images, verify operations, clone disks.
Refactored version with improved code structure and error handling.
"""
import os
import sys
import subprocess
import logging

# Setup basic logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - DiskImager - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def ensure_elevated_privileges():
    """Re-execute with pkexec if not running as root."""
    if os.geteuid() == 0:
        logger.info("Running as root")
        return
    
    logger.info("Requesting elevation via pkexec...")
    
    # Get the absolute path to the Python interpreter
    python_exe = sys.executable
    
    # Preserve DISPLAY and XAUTHORITY for X11
    env = os.environ.copy()
    display = env.get('DISPLAY', ':0')
    
    try:
        # Use subprocess with proper environment
        result = subprocess.run(
            ['pkexec', 'env', f'DISPLAY={display}', 'XAUTHORITY=' + env.get('XAUTHORITY', os.path.expanduser('~/.Xauthority')), python_exe] + sys.argv,
            env=env
        )
        sys.exit(result.returncode)
    
    except FileNotFoundError:
        logger.error("pkexec not found. Install: sudo apt install policykit-1")
        logger.error("Cannot continue without elevation for disk operations.")
        sys.exit(1)

# Call BEFORE importing GTK
ensure_elevated_privileges()
import gi
import subprocess
import time
#import os
#import sys
import threading
import fcntl
import signal
import re
import shutil
#import logging
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
        return self._format_bytes(self.size_bytes, base=1000)

    @property
    def filesystem_size_human(self) -> str:
        """Return human-readable filesystem size."""
        return self._get_filesystem_size()
    
    def _get_filesystem_size(self) -> str:
        """Get total size of all partitions on this disk."""
        try:
            # Query lsblk for all partitions on this disk
            result = subprocess.run(
                ["lsblk", "-b", "-o", "NAME,SIZE,TYPE", self.path],
                capture_output=True,
                text=True,
                timeout=5,
                check=False
            )
            if result.returncode == 0 and result.stdout:
                lines = result.stdout.strip().split('\n')
                total_partition_size = 0
                
                # Skip header (first line) and only sum partition lines
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) >= 3:
                        # Check if this is a partition (not the disk itself)
                        if parts[-1] == 'part':
                            try:
                                size = int(parts[1])
                                total_partition_size += size
                            except ValueError:
                                continue
                
                # If we found partitions, return their total size
                if total_partition_size > 0:
                    return self._format_bytes(total_partition_size, base=1024)
        except Exception as e:
            logger.debug(f"Could not get filesystem size for {self.name}: {e}")
        
        # If no partitions found, return a placeholder (not the disk size!)
        return "N/A"
    

    @staticmethod
    def _format_bytes(n: float, base: int = 1024) -> str:
        """Format bytes to human-readable format.
        
        Args:
            n: Number of bytes
            base: 1000 (decimal) or 1024 (binary, default)
        """
        units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
        n = float(n)
        for unit in units:
            if abs(n) < base or unit == units[-1]:
                return f"{n:.1f} {unit}"
            n /= base
        return f"{n:.1f} {units[-1]}"

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

    # Track partition info for later remounting
    discovered_partitions: List[PartitionInfo] = field(default_factory=list)

    def __init__(self):
        # Page 1: Read/Write
        self.selected_disk: str = ""
        self.image_path: str = ""
        self.block_size: str = "4M"
        self.disable_automount: bool = False
        self.unmount_and_remount: bool = False
        
        # Page 2: Verify
        self.verify_disk: Optional[str] = None
        self.verify_image_path: Optional[str] = None
        self.verify_disk_actual_size: int = 0
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
                ["lsblk", "-d", "-J", "-o", "NAME,PATH,TYPE"],
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
                    path = device["path"]
                    # Use blockdev to get true physical disk size
                    size_bytes = DiskManager._get_disk_size_bytes(path)
                    if size_bytes <= 0:
                        logger.warning(f"Could not determine size for {device['name']}")
                        continue
                        
                    disk = DiskInfo(
                        name=device["name"],
                        path=path,
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
    def _get_disk_size_bytes(path: str) -> int:
        """Get true physical disk size from /sys/block."""
        try:
            # Extract disk name from path (e.g., /dev/sda → sda)
            disk_name = Path(path).name
            size_file = Path("/sys/block") / disk_name / "size"
            
            if not size_file.exists():
                logger.debug(f"Size file not found for {path}")
                return 0
                
            # Read sectors and multiply by 512 bytes per sector
            sectors = int(size_file.read_text().strip())
            size_bytes = sectors * 512
            logger.debug(f"Got {disk_name} size: {size_bytes} bytes")
            return size_bytes
        except Exception as e:
            logger.debug(f"Failed to read size for {path}: {e}")
            return 0


    @staticmethod
    def get_disk_size(path: str) -> int:
        """Public interface to get disk size in bytes."""
        return DiskManager._get_disk_size_bytes(path)




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
    
    def __init__(
        self,
        source: str,
        destination: str,
        block_size: str = "4M",
        operation_type: 'OperationType' = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ):
        """Initialize disk operation handler."""
        self.source = source
        self.destination = destination
        self.block_size = block_size
        self.operation_type = operation_type
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event or threading.Event()

    def execute(self) -> Tuple[bool, str]:
        """Execute the operation based on operation_type."""
        if self.operation_type == OperationType.READ:
            return self.read_disk_to_image(
                self.source,
                self.destination,
                self.block_size,
                self.progress_callback,
                self.cancel_event
            )
        elif self.operation_type == OperationType.WRITE:
            return self.write_image_to_disk(
                self.source,
                self.destination,
                self.block_size,
                self.progress_callback,
                self.cancel_event
            )
        elif self.operation_type == OperationType.CLONE:
            return self.clone_disk(
                self.source,
                self.destination,
                self.block_size,
                self.progress_callback,
                self.cancel_event
            )
        else:
            return False, "Unknown operation type"

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
            # Ensure disk_path has /dev/ prefix
            if not disk_path.startswith('/dev/'):
                disk_path = f'/dev/{disk_path}'
            
            
            disk_size = DiskManager.get_disk_size_bytes(disk_path)
            if disk_size <= 0:
                return False, "Could not determine disk size"

            logger.info(f"Reading {disk_path} ({disk_size} bytes) to {image_path}")

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
                text=True,
                bufsize=1  # Line buffering
            )

            bytes_read = 0
            try:
                for line in process.stderr:
                    if cancel_event and cancel_event.is_set():
                        process.terminate()
                        process.wait()
                        return False, "Operation cancelled by user"

                    line = line.strip()
                    if line:
                        logger.debug(f"dd progress: {line}")
                        try:
                            bytes_read = int(line.split()[0])
                            if progress_callback:
                                progress_callback(bytes_read, disk_size, line)
                        except (ValueError, IndexError):
                            pass

            except Exception as e:
                logger.warning(f"Error reading progress: {e}")

            # Wait for process to complete
            returncode = process.wait()
            
            logger.info(f"dd process exited with code: {returncode}")
            logger.info(f"Final bytes read: {bytes_read}, Expected: {disk_size}")

            if returncode == 0:
                logger.info(f"Successfully read {disk_path} to {image_path}")
                return True, "Read successful"
            else:
                stderr_output = process.stderr.read() if process.stderr else "No error message"
                logger.error(f"dd command failed with code {returncode}: {stderr_output}")
                return False, f"dd command failed with code {returncode}"

        except Exception as e:
            logger.error(f"Error reading disk: {e}", exc_info=True)
            return False, str(e)


    @staticmethod
    def write_image_to_disk(
        image_path: str,
        disk_path: str,
        block_size: str = "4M",
        progress_callback: Optional[Callable[[int, int, str], None]] = None,  # ← Updated type hint
        cancel_event: Optional[threading.Event] = None
    ) -> Tuple[bool, str]:
        """Write image to disk."""
        try:
            # Ensure disk_path has /dev/ prefix
            if not disk_path.startswith('/dev/'):
                disk_path = f'/dev/{disk_path}'
            
            image_size = os.path.getsize(image_path)
            if image_size <= 0:
                return False, "Image file is empty or not found"

            logger.info(f"Writing {image_path} ({image_size} bytes) to {disk_path}")

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
                text=True,
                bufsize=1  # Line buffering
            )

            bytes_written = 0
            try:
                for line in process.stderr:
                    if cancel_event and cancel_event.is_set():
                        process.terminate()
                        process.wait()
                        return False, "Operation cancelled by user"

                    line = line.strip()
                    if line:
                        logger.debug(f"dd progress: {line}")
                        try:
                            bytes_written = int(line.split()[0])
                            if progress_callback:
                                progress_callback(bytes_written, image_size, line)  # ← Pass raw line here
                        except (ValueError, IndexError):
                            pass

            except Exception as e:
                logger.warning(f"Error reading progress: {e}")

            # Wait for process to complete
            returncode = process.wait()
            
            logger.info(f"dd process exited with code: {returncode}")
            logger.info(f"Final bytes written: {bytes_written}, Expected: {image_size}")

            # Sync filesystem
            logger.info("Running sync to ensure data is written...")
            subprocess.run(["sudo", "sync"], check=False)

            if returncode == 0:
                logger.info(f"Successfully wrote {image_path} to {disk_path}")
                return True, "Write successful"
            else:
                stderr_output = process.stderr.read() if process.stderr else "No error message"
                logger.error(f"dd command failed with code {returncode}: {stderr_output}")
                return False, f"dd command failed with code {returncode}"

        except Exception as e:
            logger.error(f"Error writing image: {e}", exc_info=True)
            return False, str(e)


    @staticmethod
    def hash_file(file_path: str, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> str:
        """Compute SHA256 hash of a file or disk device."""
        try:
            h = hashlib.sha256()
            total_size = os.path.getsize(file_path) if os.path.isfile(file_path) else os.path.getsize(f"/sys/block/{Path(file_path).name}/size") * 512
            bytes_read = 0
            
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(8192)  # CHUNK = 8192
                    if not chunk:
                        break
                    h.update(chunk)
                    bytes_read += len(chunk)
                    if progress_callback:
                        progress_callback(bytes_read, total_size, f"Hashing: {file_path}")
            
            return h.hexdigest()
        except Exception as e:
            logger.error(f"Error computing hash: {e}", exc_info=True)
            raise




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
                bufsize=1,
                universal_newlines=True
            )

            bytes_cloned = 0
            while True:
               
                line = process.stderr.readline()
                if not line:
                    break

            # Handle both newlines and carriage returns
                line = line.rstrip('\r\n')
                if not line:
                    continue
                
                
                
                
                
                try:
                    bytes_cloned = int(line.split()[0])
                    if progress_callback:
                        # Calculate progress percentage and formatted sizes
                        percentage = (bytes_cloned / source_size) * 100
                        cloned_gb = bytes_cloned / (1024**3)
                        total_gb = source_size / (1024**3)
                        status = f"Cloning: {cloned_gb:.1f} GB of {total_gb:.1f} GB ({percentage:.1f}%)"
                        progress_callback(bytes_cloned, source_size, status)

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
        self.window.set_title("AnyDistro Disk Imager")


        # Cache common widgets for quick access
        self.progress_bar = self.builder.get_object("imageProgressBar")
        self.percentage_label = self.builder.get_object("diskImageProgressPercentageLabel")
        self.progress_bar = self.builder.get_object("imageProgressBar")
        self.image_entry = self.builder.get_object("imageFileText")
        
        # Initialize state and managers
        self.state = AppState()
        self.disk_manager = DiskManager()
        self.mount_manager = MountManager()
        
        # Operation flags
        self.is_operating = False
        self.operation_stopped = False

        # ===== PAGE 1: READ/WRITE OPTIONS =====
    
        # Block Size ComboBox
        self.combo_block_size = self.builder.get_object("SelectedBlockSize")
        if self.combo_block_size:
            # Populate with predefined block sizes
            list_store = Gtk.ListStore(str)
            for bs in BLOCK_SIZES:
                list_store.append([bs])
            self.combo_block_size.set_model(list_store)
            self.combo_block_size.set_active(0)  # Default to 4M
            self.combo_block_size.connect("changed", self.on_block_size_changed)
        
        # Disable Automount Checkbox
        self.check_disable_automount = self.builder.get_object("OptionDisableAutomounter")
        if self.check_disable_automount:
            self.check_disable_automount.set_active(False)
            self.check_disable_automount.connect("toggled", self.on_disable_automount_toggled)
        
        # Unmount and Remount Checkbox
        self.check_unmount_remount = self.builder.get_object("OptionUnmountandRemount")
        if self.check_unmount_remount:
            self.check_unmount_remount.set_active(False)
            self.check_unmount_remount.connect("toggled", self.on_unmount_remount_toggled)
        #===========PAGE3===========================
        self.clone_progress_bar = self.builder.get_object("cloneprogressbar")
        self.clone_progress_label = self.builder.get_object("CloneDiskProgressLabel")

        if not self.clone_progress_bar:
            logger.error("cloneprogressbar widget not found in Glade!")
        if not self.clone_progress_label:
            logger.error("CloneDiskProgressLabel widget not found in Glade!")
        
        # Page 3: Clone Disk UI Setup
        clonediskcombobox1 = self.builder.get_object("clonediskcombobox1")
        clonediskcombobox2 = self.builder.get_object("clonediskcombobox2")

        # Clear any existing renderers
        clonediskcombobox1.clear()
        clonediskcombobox2.clear()

        # Use the same liststore1 for both (disk list)
        liststore1 = self.builder.get_object("liststore1")
        clonediskcombobox1.set_model(liststore1)
        clonediskcombobox2.set_model(liststore1)

        # Add cell renderers for disk name and size
        for combo in [clonediskcombobox1, clonediskcombobox2]:
            renderer_name = Gtk.CellRendererText()
            combo.pack_start(renderer_name, True)
            combo.add_attribute(renderer_name, "text", 0)  # Disk name
            
            renderer_size = Gtk.CellRendererText()
            combo.pack_start(renderer_size, True)
            combo.add_attribute(renderer_size, "text", 2)  # Total size

        # ===== INITIALIZE COMBOBOX WITH GLADE LISTSTORE =====
        # Initialize diskSelectCombo with existing liststore1 from Glade
        disk_combo = self.builder.get_object("diskSelectCombo")
        if disk_combo:
            logger.info("diskSelectCombo found")
            
            # GET the ListStore that's already defined in Glade (NOT creating a new one)
            disk_liststore = self.builder.get_object("liststore1")
            if disk_liststore:
                logger.info(f"Using existing liststore1 from Glade with {disk_liststore.get_n_columns()} columns")
                
                # Clear any existing rows
                disk_liststore.clear()
                
               # Clear any existing renderers from Glade (it only has 1, we need 3)
                disk_combo.clear()

                # Add the 3 cell renderers
                for col_index in range(3):
                    renderer = Gtk.CellRendererText()
                    disk_combo.pack_start(renderer, True)
                    disk_combo.add_attribute(renderer, "text", col_index)
                    logger.debug(f"Added renderer for column {col_index}")
                # Add header row
                disk_liststore.append(["Select Disk", " File System Size", "Total Disk Size"])
                logger.info("Added header row to diskSelectCombo model")
            else:
                logger.error("liststore1 not found in Glade file!")
        else:
            logger.error("diskSelectCombo not found in Glade!")
        
        # Setup UI (cache widget references)
        self.setup_ui()
        
        # Connect all signals
        self.connect_signals()

#========================================================================
    # PAGE 2: VERIFY TAB SETUP
    # ========================================================================
    
            # Get the shared liststore from Page 1 (disk list)
        liststore_disks = self.builder.get_object("liststore1")
        self.entry_checksum_output = self.builder.get_object("ChecksumOutputEntry")
            # Setup verifydiskcombobox with the disk liststore
        self.combo_box_verify_disk = self.builder.get_object("verifydiskcombobox")
        if self.combo_box_verify_disk:
            self.combo_box_verify_disk.clear()
            self.combo_box_verify_disk.set_model(liststore_disks)
                
                # Create and pack renderers (same as Page 1)
            renderer1 = Gtk.CellRendererText()
            renderer1.props.alignment = Pango.Alignment.LEFT
            self.combo_box_verify_disk.pack_start(renderer1, True)
            self.combo_box_verify_disk.add_attribute(renderer1, "text", 0)  # Disk name
                
            renderer2 = Gtk.CellRendererText()
            renderer2.props.alignment = Pango.Alignment.LEFT
            self.combo_box_verify_disk.pack_start(renderer2, True)
            self.combo_box_verify_disk.add_attribute(renderer2, "text", 1)  # Filesystem size
                
            renderer3 = Gtk.CellRendererText()
            renderer3.props.alignment = Pango.Alignment.LEFT
            self.combo_box_verify_disk.pack_start(renderer3, True)
            self.combo_box_verify_disk.add_attribute(renderer3, "text", 2)  # Total size
                
            self.combo_box_verify_disk.set_active(0)
            
            # Setup checksumCombobox2 with liststore3
        liststore_hash = self.builder.get_object("liststore3")
        self.combo_box_verify_hash2 = self.builder.get_object("checksumCombobox2")
        if self.combo_box_verify_hash2 and liststore_hash:
            self.combo_box_verify_hash2.clear()
            self.combo_box_verify_hash2.set_model(liststore_hash)
                
                # Create and pack renderer
            renderer_hash = Gtk.CellRendererText()
            renderer_hash.props.alignment = Pango.Alignment.LEFT
            self.combo_box_verify_hash2.pack_start(renderer_hash, True)
            self.combo_box_verify_hash2.add_attribute(renderer_hash, "text", 0)
                
            self.combo_box_verify_hash2.set_active(2)  # Default to SHA256
            
            # Get UI elements
            self.entry_verify_image = self.builder.get_object("imageverifyentry")
            self.entry_checksum_output = self.builder.get_object("ChecksumOutputEntry")
            self.progress_bar_verify = self.builder.get_object("verifyProgressBar")

        # Setup checksumCombobox1 with liststore3 (verify disk hash type)
        liststore_hash = self.builder.get_object("liststore3")
        self.combo_box_verify_hash1 = self.builder.get_object("checksumCombobox1")
        if self.combo_box_verify_hash1 and liststore_hash:
            self.combo_box_verify_hash1.clear()  # Clear any existing renderers
            self.combo_box_verify_hash1.set_model(liststore_hash)
            
            # Create and pack renderer
            renderer_hash = Gtk.CellRendererText()
            renderer_hash.props.alignment = Pango.Alignment.LEFT
            self.combo_box_verify_hash1.pack_start(renderer_hash, True)
            self.combo_box_verify_hash1.add_attribute(renderer_hash, "text", 0)
            
            self.combo_box_verify_hash1.set_active(2)  # Default to SHA256



                
                # Load initial disk list
            logger.info("Calling on_refresh_disks()")
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


    def _verify_operation(self) -> bool:
        """
        Verify read/write operation via SHA256 hash comparison.
        Updates progress: 0-50% image file, 50-100% disk.
        Returns True if hashes match, False otherwise.
        """
        try:
            image_path = self.state.image_path
            disk_path = self.state.selected_disk

             # Ensure disk path has /dev/ prefix
            if not disk_path.startswith('/dev/'):
                disk_path = f'/dev/{disk_path}'
        
            
            if not os.path.exists(image_path):
                GLib.idle_add(lambda: self._show_error(f"Image file not found: {image_path}"))
                return False
            
            # Update UI to show verification is starting
            GLib.idle_add(lambda: self.progress_bar.set_text("Verifying image file..."))
            
            # Get file sizes for progress calculation
            image_size = os.path.getsize(image_path)
            try:
                disk_size = int(open(f"/sys/block/{os.path.basename(disk_path)}/size").read().strip()) * 512
            except (FileNotFoundError, ValueError):
                # Fallback: use blockdev
                try:
                    result = subprocess.run(
                        ["sudo", "blockdev", "--getsize64", disk_path],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    disk_size = int(result.stdout.strip()) if result.returncode == 0 else image_size
                except Exception:
                    disk_size = image_size
            
            # Compute image hash (0-50% progress)
            logger.info(f"Computing SHA256 for image: {image_path}")
            image_hash = hashlib.sha256()
            bytes_read = 0
            chunk_size = 8192
            
            with open(image_path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    image_hash.update(chunk)
                    bytes_read += len(chunk)
                    
                    # Progress: 0-50% for image
                    progress = (bytes_read / image_size) * 50
                    GLib.idle_add(
                        lambda p=progress, b=bytes_read, s=image_size: (
                            self.percentage_label.set_text(f"{int(p)}%"),
                            self.progress_bar.set_fraction(p / 100),
                            self.progress_bar.set_text(
                                f"Verifying image... {self.format_bytes(b)} / {self.format_bytes(s)}"
                            )
                        )
                    )
            
            image_hash_value = image_hash.hexdigest()
            logger.info(f"Image SHA256: {image_hash_value}")
            
            # Compute disk hash (50-100% progress)
            logger.info(f"Computing SHA256 for disk: {disk_path}")
            GLib.idle_add(lambda: self.progress_bar.set_text("Verifying disk..."))

            disk_hash = hashlib.sha256()
            disk_bytes_hashed = 0
            bytes_to_verify = self.state.image_size  # Only hash what was written/read

            with open(disk_path, 'rb') as f:
                while disk_bytes_hashed < bytes_to_verify:
                    chunk_to_read = min(chunk_size, bytes_to_verify - disk_bytes_hashed)
                    chunk = f.read(chunk_to_read)
                    if not chunk:
                        break
                    disk_hash.update(chunk)
                    disk_bytes_hashed += len(chunk)
                    
                    # Progress: 50-100% for disk
                    progress = 50 + ((disk_bytes_hashed / bytes_to_verify) * 50)
                    GLib.idle_add(
                        lambda p=progress, b=disk_bytes_hashed, s=bytes_to_verify: (
                            self.percentage_label.set_text(f"{int(p)}%"),
                            self.progress_bar.set_fraction(p / 100),
                            self.progress_bar.set_text(
                                f"Verifying disk... {self.format_bytes(b)} / {self.format_bytes(s)}"
                            )
                        )
                    )



            
            disk_hash_value = disk_hash.hexdigest()
            logger.info(f"Disk SHA256: {disk_hash_value}")
            
            # Compare hashes
            if image_hash_value == disk_hash_value:
                GLib.idle_add(lambda: self.progress_bar.set_text('Operation Completed Sucessfully'))

                logger.info("Verification PASSED: Hashes match")
                return True
            else:
                logger.error(f"Verification FAILED: Hash mismatch!\nImage: {image_hash_value}\nDisk: {disk_hash_value}")
                GLib.idle_add(lambda: self.progress_bar.set_text('Operation Completed'))
                GLib.idle_add(
                    lambda: self._show_error(
                        f"Verification failed: Hash mismatch!\nImage SHA256: {image_hash_value}\nDisk SHA256: {disk_hash_value}"
                    )
                )
                return False
        
        except Exception as e:
            logger.error(f"Verification error: {e}", exc_info=True)
            GLib.idle_add(lambda msg=str(e): self._show_error(f"Verification error: {msg}"))
            return False
        
    def _verify_clone_operation(self) -> bool:
        """
        Verify clone operation via SHA256 hash comparison of source and target disks.
        Updates progress: 0-50% source disk, 50-100% target disk.
        Returns True if hashes match, False otherwise.
        """
        try:
            source_path = self.state.clone_source
            target_path = self.state.clone_target
            
            # Ensure disk paths have /dev/ prefix
            if not source_path.startswith('/dev/'):
                source_path = f'/dev/{source_path}'
            if not target_path.startswith('/dev/'):
                target_path = f'/dev/{target_path}'
            
            # Update UI to show verification is starting
            GLib.idle_add(lambda: self.clone_progress_bar.set_text("Verifying clone..."))
            
            # Get disk sizes
            try:
                source_size = int(open(f"/sys/block/{os.path.basename(source_path)}/size").read().strip()) * 512
            except (FileNotFoundError, ValueError):
                try:
                    result = subprocess.run(
                        ["sudo", "blockdev", "--getsize64", source_path],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    source_size = int(result.stdout.strip()) if result.returncode == 0 else 0
                except Exception:
                    source_size = 0
            
            try:
                target_size = int(open(f"/sys/block/{os.path.basename(target_path)}/size").read().strip()) * 512
            except (FileNotFoundError, ValueError):
                try:
                    result = subprocess.run(
                        ["sudo", "blockdev", "--getsize64", target_path],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    target_size = int(result.stdout.strip()) if result.returncode == 0 else source_size
                except Exception:
                    target_size = source_size
            
            if source_size == 0:
                GLib.idle_add(lambda: self._show_error(f"Could not determine source disk size: {source_path}"))
                return False
            
            # Compute source disk hash (0-50% progress)
            logger.info(f"Computing SHA256 for source disk: {source_path}")
            source_hash = hashlib.sha256()
            source_bytes_read = 0
            chunk_size = 8192
            
            with open(source_path, 'rb') as f:
                while source_bytes_read < source_size:
                    chunk_to_read = min(chunk_size, source_size - source_bytes_read)
                    chunk = f.read(chunk_to_read)
                    if not chunk:
                        break
                    source_hash.update(chunk)
                    source_bytes_read += len(chunk)
                    
                    # Progress: 0-50% for source
                    progress = (source_bytes_read / source_size) * 50
                    GLib.idle_add(
                        lambda p=progress, b=source_bytes_read, s=source_size: (
                            self.clone_progress_label.set_text(f"{int(p)}%"),
                            self.clone_progress_bar.set_fraction(p / 100),
                            self.clone_progress_bar.set_text(
                                f"Verifying source... {self.format_bytes(b)} / {self.format_bytes(s)}"
                            )
                        )
                    )
            
            source_hash_value = source_hash.hexdigest()
            logger.info(f"Source disk SHA256: {source_hash_value}")
            
            # Compute target disk hash (50-100% progress)
            logger.info(f"Computing SHA256 for target disk: {target_path}")
            GLib.idle_add(lambda: self.clone_progress_bar.set_text("Verifying target..."))
            
            target_hash = hashlib.sha256()
            target_bytes_read = 0
            bytes_to_verify = source_bytes_read  # Only hash what was cloned
            
            with open(target_path, 'rb') as f:
                while target_bytes_read < bytes_to_verify:
                    chunk_to_read = min(chunk_size, bytes_to_verify - target_bytes_read)
                    chunk = f.read(chunk_to_read)
                    if not chunk:
                        break
                    target_hash.update(chunk)
                    target_bytes_read += len(chunk)
                    
                    # Progress: 50-100% for target
                    progress = 50 + ((target_bytes_read / bytes_to_verify) * 50)
                    GLib.idle_add(
                        lambda p=progress, b=target_bytes_read, s=bytes_to_verify: (
                            self.clone_progress_label.set_text(f"{int(p)}%"),
                            self.clone_progress_bar.set_fraction(p / 100),
                            self.clone_progress_bar.set_text(
                                f"Verifying target... {self.format_bytes(b)} / {self.format_bytes(s)}"
                            )
                        )
                    )
            
            target_hash_value = target_hash.hexdigest()
            logger.info(f"Target disk SHA256: {target_hash_value}")
            
            # Compare hashes
            if source_hash_value == target_hash_value:
                GLib.idle_add(lambda: self.clone_progress_bar.set_text('Clone Completed Successfully'))
                logger.info("Clone verification PASSED: Hashes match")
                return True
            else:
                logger.error(f"Clone verification FAILED: Hash mismatch!\nSource: {source_hash_value}\nTarget: {target_hash_value}")
                GLib.idle_add(lambda: self.clone_progress_bar.set_text('Clone Completed'))
                GLib.idle_add(
                    lambda: self._show_error(
                        f"Clone verification failed: Hash mismatch!\nSource SHA256: {source_hash_value}\nTarget SHA256: {target_hash_value}"
                    )
                )
                return False
        
        except Exception as e:
            logger.error(f"Clone verification error: {e}", exc_info=True)
            GLib.idle_add(lambda msg=str(e): self._show_error(f"Clone verification error: {msg}"))
            return False






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
        
        # ===== DEBUG: Verify model structure =====
        logger.info(f"Model type: {type(model)}")
        logger.info(f"Model has {model.get_n_columns()} columns")
        
        # Verify column types
        for i in range(model.get_n_columns()):
            col_type = model.get_column_type(i)
            logger.info(f"  Column {i}: {col_type}")
        
        # Clear existing entries (but keep the "Select Disk" header)
        model.clear()
        
        # Test: Append header row
        header_row = ["Select Disk", " File System Size", "Total Disk Size"]
        logger.info(f"Appending header: {header_row} (length: {len(header_row)})")
        try:
            model.append(header_row)
            logger.info("Header row appended successfully")
        except Exception as e:
            logger.error(f"ERROR appending header: {e}")
            import traceback
            traceback.print_exc()
            return
        
        try:
            disks = self.disk_manager.discover_disks()
            logger.info(f"Discovered {len(disks)} disks")
            for disk in disks:
                disk_row = [disk.name, disk.filesystem_size_human, disk.size_human]
                logger.debug(f"Appending disk row: {disk_row} (length: {len(disk_row)})")
                try:
                    model.append(disk_row)
                except Exception as e:
                    logger.error(f"ERROR appending disk row {disk_row}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            disk_combo.set_active(0)  # Select "Select Disk" by default
            logger.info("Disk refresh completed successfully")
        except Exception as e:
            logger.error(f"Error discovering disks: {e}")
            import traceback
            traceback.print_exc()

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


    def _prepare_disk_operation(self, disk_path: str) -> bool:
        """Prepare disk for operation (unmount/disable automount).
        
        Returns True if successful, False if user should cancel.
        """
        logger.info(f"Preparing disk {disk_path} for operation...")
        
        # Discover partitions on selected disk
        self.state.discovered_partitions = DiskManager.discover_partitions(disk_path)
        logger.debug(f"Discovered {len(self.state.discovered_partitions)} partitions")
        
        # Unmount partitions if option is enabled
        if self.state.unmount_and_remount:
            logger.info("Unmounting partitions...")
            success = MountManager.unmount_partitions(self.state.discovered_partitions)
            
            # If normal unmount fails, try force unmount
            if not success:
                logger.warning("Normal unmount failed, attempting force unmount...")
                MountManager.force_unmount_partitions(self.state.discovered_partitions)
            
            # Verify no mounts remain
            if not self._verify_no_mounts(disk_path, timeout=10):
                logger.error("Could not unmount all partitions!")
                self._show_error_dialog("Mount Error", 
                    "Could not unmount all partitions. Operation may fail.")
                return False
        
        # Stop automount services if option is enabled
        if self.state.disable_automount:
            logger.info("Stopping automount services...")
            stopped, success = MountManager.stop_automount_services()
            self.state.stopped_services = stopped
            
            if stopped:
                logger.info(f"Stopped services: {', '.join(stopped)}")
            
            # Also kill automount processes
            killed, success = MountManager.kill_automount_processes()
            if killed:
                logger.info(f"Killed processes: {', '.join(killed)}")
        
        return True

    def _verify_no_mounts(self, disk_path: str, timeout: int = 10) -> bool:
        """Verify that disk has no mounted partitions."""
        end_time = time.time() + timeout
        
        while time.time() < end_time:
            partitions = DiskManager.discover_partitions(disk_path)
            mounted = any(p.is_mounted() for p in partitions)
            
            if not mounted:
                logger.info(f"Verified: no partitions mounted on {disk_path}")
                return True
            
            time.sleep(0.5)
        
        logger.warning(f"Timeout: partitions still mounted on {disk_path}")
        return False

    def _cleanup_after_operation(self):
        """Clean up after operation completes (remount/restart services)."""
        logger.info("Cleaning up after operation...")
        
        # Remount partitions if they were unmounted
        if self.state.unmount_and_remount:
            logger.info("Remounting partitions...")
            success = MountManager.remount_partitions(self.state.discovered_partitions)
            if success:
                logger.info("Successfully remounted partitions")
            else:
                logger.warning("Some partitions failed to remount")
        
        # Restart automount services if they were stopped
        if self.state.disable_automount and self.state.stopped_services:
            logger.info("Restarting automount services...")
            success = MountManager.start_automount_services(self.state.stopped_services)
            if success:
                logger.info("Successfully restarted services")
            else:
                logger.warning("Some services failed to restart")
        
        # Clear state
        self.state.discovered_partitions = []
        self.state.stopped_services = []







    #def on_read_clicked(self, widget) -> None:
        #"""Handle read disk button."""
        #if not self._validate_read_operation():
        #    return
        #self._start_operation(OperationType.READ)

    #def on_write_clicked(self, widget) -> None:
       # """Handle write image button."""
       # if not self._validate_write_operation():
          #  return
        #self._start_operation(OperationType.WRITE)

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


    def on_block_size_changed(self, combo):
        """Handle block size selection change."""
        active_iter = combo.get_active_iter()
        if active_iter is not None:
            model = combo.get_model()
            self.state.block_size = model[active_iter][0]
            logger.info(f"Block size selected: {self.state.block_size}")

    def on_disable_automount_toggled(self, checkbox):
        """Handle disable automount checkbox toggle."""
        self.state.disable_automount = checkbox.get_active()
        status = "enabled" if self.state.disable_automount else "disabled"
        logger.info(f"Disable automount: {status}")

    def on_unmount_remount_toggled(self, checkbox):
        """Handle unmount/remount checkbox toggle."""
        self.state.unmount_and_remount = checkbox.get_active()
        status = "enabled" if self.state.unmount_and_remount else "disabled"
        logger.info(f"Unmount/remount: {status}")



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

   # def _start_operation(self, op_type: OperationType) -> None:
       # """Start a disk operation in a background thread."""
      #  self.state.operation_cancelled = False
      #  thread = threading.Thread(
      ##      target=self._execute_operation,
       #     args=(op_type,),
       #     daemon=True
       # )
       # thread.start()

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

    def format_bytes(self, n, base=1000, units=None):
        """Format bytes into human-readable format."""
        n = float(n)
        if units is None:
            units = ['B','KiB','MiB','GiB','TiB','PiB'] if base==1024 else ['B','K','M','G','T','P']
        for unit in units:
            if abs(n) < base or unit == units[-1]:
                return f"{n:.1f}{unit}"
            n /= base

    def _update_progress(self, bytes_current: int, bytes_total: int, raw_dd_line: str):
        percentage = (bytes_current / bytes_total * 100) if bytes_total > 0 else 0
        GLib.idle_add(lambda: self.percentage_label.set_text(f"{percentage:.1f}%"))
        GLib.idle_add(lambda: self.progress_bar.set_text(raw_dd_line))
        GLib.idle_add(lambda: self.progress_bar.set_fraction(bytes_current / bytes_total))
        GLib.idle_add(lambda: self.clone_progress_label.set_text(f"{percentage:.1f}%"))
        GLib.idle_add(lambda: self.clone_progress_bar.set_text(raw_dd_line))
        GLib.idle_add(lambda: self.clone_progress_bar.set_fraction(bytes_current / bytes_total))



    def _handle_operation_completion(self, success: bool, message: str, operation_type: str, image_path: str = None, disk_path: str = None):
        """Handle operation completion and trigger verification."""
        self.progress_bar.set_fraction(0.0)
        
        if success:
            logger.info(f"{operation_type} completed successfully")
            
            # Start verification in background thread
            if operation_type == "read":
                verify_thread = threading.Thread(
                    target=self._verify_operation,
                    args=(image_path, disk_path),
                    daemon=True
                )
                verify_thread.start()
            elif operation_type == "write":
                verify_thread = threading.Thread(
                    target=self._verify_operation,
                    args=(image_path, disk_path),
                    daemon=True
                )
                verify_thread.start()

        

    def _update_verify_progress(self, current: int, total: int, status: str) -> None:
        """Update progress during verify operations."""
        if total <= 0:
            return
        fraction = min(1.0, current / total)
        percentage = (current / total) * 100

        # Format bytes nicely
        current_str = self.format_bytes(current)
        total_str = self.format_bytes(total)

        progress_text = f"{status}: {percentage:.1f}% ({current_str} / {total_str})"

        GLib.idle_add(self.progress_bar_verify.set_fraction, fraction)
        GLib.idle_add(self.progress_bar_verify.set_text, progress_text)

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
            self.state.verify_image_path = filename
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
            self.state.verify_disk = f"/dev/{disk_name}" if disk_name != "Select Disk" else ""
            logger.info(f"Verify disk selected: {self.state.verify_disk}")

    def on_hash_type_disk_changed(self, widget):
        """Handler for hash algorithm selection (disk)."""
        active_iter = widget.get_active_iter()
        if active_iter is not None:
            model = widget.get_model()
            hash_type = model[active_iter][0]
            self.state.hash_algorithm_disk = hash_type
            logger.info(f"Hash algorithm (disk) changed to: {hash_type}")

    def on_hash_type_image_changed(self, widget):
        """Handler for hash algorithm selection (image)."""
        active_iter = widget.get_active_iter()
        if active_iter is not None:
            model = widget.get_model()
            hash_type = model[active_iter][0]
            self.state.hash_algorithm_image = hash_type
            logger.info(f"Hash algorithm (image) changed to: {hash_type}")

    def on_verify_image_path_changed(self, widget):
        """Handler for image path entry changes in verify tab."""
        self.state.verify_image_path = widget.get_text()
        logger.debug(f"Verify image path changed to: {self.state.verify_image_path}")


  # PAGE 2: VERIFY HANDLERS
    # ========================================================================
    
    def on_verifydiskcombobox_changed(self, combo):
        """Handle disk selection in verify tab."""
        try:
            active_iter = combo.get_active_iter()
            if active_iter is None:
                self.state.verify_disk = None
                return
            
            model = combo.get_model()
            selected_disk = model.get_value(active_iter, 0)
            self.state.verify_disk = "/dev/" + selected_disk
            print(f"Selected verify disk: {self.state.verify_disk}")
        except Exception as e:
            print(f"Error in on_verifydiskcombobox_changed: {e}")
    
    def on_checksumCombobox1_changed(self, combo):
        """Handle hash algorithm selection for disk."""
        try:
            active_iter = combo.get_active_iter()
            if active_iter is None:
                return
            
            model = combo.get_model()
            algorithm = model.get_value(active_iter, 0)
            self.state.hash_algorithm_disk = algorithm
            print(f"Selected disk hash algorithm: {algorithm}")
        except Exception as e:
            print(f"Error in on_checksumCombobox1_changed: {e}")
    
    def on_checksumCombobox2_changed(self, combo):
        """Handle hash algorithm selection for image."""
        try:
            active_iter = combo.get_active_iter()
            if active_iter is None:
                return
            
            model = combo.get_model()
            algorithm = model.get_value(active_iter, 0)
            self.state.hash_algorithm_image = algorithm
            print(f"Selected image hash algorithm: {algorithm}")
        except Exception as e:
            print(f"Error in on_checksumCombobox2_changed: {e}")
    
    def on_refreshDiskButton1_clicked(self, widget):
        """Refresh disk list in verify tab."""
        try:
            print("Refreshing disk list for verify tab")
            combo_box5 = self.builder.get_object("verifydiskcombobox")
            
            if not combo_box5:
                return
            
            # Get the list store
            listStore = combo_box5.get_model()
            if listStore:
                listStore.clear()
                listStore.append(['Select Disk', ' File System Size', 'Total Disk Size'])
                
                # Get disk list
                resultdisk = subprocess.run(['lsblk', '-d', '-n', '-o', 'NAME'], 
                                          capture_output=True, text=True)
                resultdisksize = subprocess.run(['lsblk', '-d', '-n', '-o', 'SIZE'], 
                                              capture_output=True, text=True)
                
                disksplitname = resultdisk.stdout.split()
                disksplitsize = resultdisksize.stdout.split()
                
                resultsplittotalsize = []
                for n in disksplitname:
                    tds = self.get_disk_size(n)
                    tdf = self.format_bytes(tds)
                    resultsplittotalsize.append(str(tdf))
                
                newtotal = resultsplittotalsize
                for n, s, t in zip(disksplitname, disksplitsize, newtotal):
                    listStore.append([n, ' ' + s, t])
                
                combo_box5.set_active(0)
        except Exception as e:
            print(f"Error refreshing disk list: {e}")
    
    def on_openimageButton2_clicked(self, widget):
        """Browse for image file in verify tab."""
        dialog = Gtk.FileChooserDialog(
            "Open Image File",
            self.window,
            Gtk.FileChooserAction.OPEN
        )
        
        dialog.add_button(Gtk.STOCK_CANCEL, -6)
        dialog.add_button(Gtk.STOCK_OPEN, -3)
        
        try:
            response = dialog.run()
            print(f"Response: {response}")
            
            if response == -3:
                filename = dialog.get_filename()
                print(f"File selected: {filename}")
                self.state.verify_image_path = filename  
                
                if self.entry_verify_image:
                    self.entry_verify_image.set_text(filename)
        
        finally:
            dialog.destroy()



    def on_generateChecksumDiskButton_clicked(self, button):
        """Generate hash for selected disk."""
        try:
            print("Checksum Disk Button Clicked")
            
            if not self.state.verify_disk:
                print("No disk selected")
                return
            
            path = Path(self.state.verify_disk)
            self.state.verify_disk_actual_size = self.get_disk_size(self.state.verify_disk)
            
            entry3 = self.builder.get_object("ChecksumOutputEntry")
            progressBar2 = self.builder.get_object("verifyProgressBar")
            
            if entry3:
                entry3.set_text("")
            if progressBar2:
                progressBar2.set_text("")
            
            def work2():
                try:
                    fhash = ""
                    fhash = self.verify_hash_file(path, self.state.verify_disk)
                finally:
                    if entry3:
                        GLib.idle_add(entry3.set_text, str(fhash))
                    if progressBar2:
                        GLib.idle_add(progressBar2.set_text, "Hash Complete")
                    print("Hash Completed")
            
            threading.Thread(target=work2, daemon=True).start()
        except Exception as e:
            print(f"Error in on_generateChecksumDiskButton_clicked: {e}")
    
    def on_generateChecksumImageButton_clicked(self, button):
        """Generate hash for selected image."""
        try:
            print("Checksum Image Button Clicked")
            
            if not self.state.verify_image_path:
                print("No image file selected")
                return
            
            path = Path(self.state.verify_image_path)
            
            entry3 = self.builder.get_object("ChecksumOutputEntry")
            progressBar2 = self.builder.get_object("verifyProgressBar")
            
            if entry3:
                entry3.set_text("")
            if progressBar2:
                progressBar2.set_text("")
            
            def work3():
                try:
                    fhash3 = ""
                    fhash3 = self.verify_hash_file(path, self.state.verify_image_path)
                finally:
                    if entry3:
                        GLib.idle_add(entry3.set_text, str(fhash3))
                    if progressBar2:
                        GLib.idle_add(progressBar2.set_text, "Hash Complete")
                    print("Hash Completed")
            
            threading.Thread(target=work3, daemon=True).start()
        except Exception as e:
            print(f"Error in on_generateChecksumImageButton_clicked: {e}")
    
    def verify_hash_file(self, path, name):
        """Calculate hash of file with progress updates."""
        try:
            algorithm = self.state.hash_algorithm_disk if name == self.state.verify_disk else self.state.hash_algorithm_image
            
            if algorithm == "MD5":
                h = hashlib.md5()
            elif algorithm == "SHA1":
                h = hashlib.sha1()
            elif algorithm == "SHA512":
                h = hashlib.sha512()
            else:  # Default to SHA256
                h = hashlib.sha256()
            
            total = os.path.getsize(path)
            read = 0
            
            progressBar2 = self.builder.get_object("verifyProgressBar")
            
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(8192)  # CHUNK size
                    if not chunk:
                        break
                    h.update(chunk)
                    read += len(chunk)
                    
                    # Update progress bar
                    if progressBar2 and total > 0:
                        frac = min(1.0, read / total)
                        GLib.idle_add(progressBar2.set_fraction, frac)
                        byteTotal = self.format_bytes(read)
                        GLib.idle_add(progressBar2.set_text, f"Hashing: {byteTotal}")
            
            result = h.hexdigest()
            print(f"Hash result: {algorithm} = {result}")
            return f"{algorithm}: {result}"
        
        except Exception as e:
            print(f"Error calculating hash: {e}")
            return f"Error: {str(e)}"







# ===================== PAGE 3: CLONE HANDLERS =====================

    def on_clonediskcombobox1_changed(self, widget):
        """Handle source disk selection (Page 3)."""
        try:
            active_iter = widget.get_active_iter()
            if active_iter is None:
                self.state.clone_source = ""
                return
            
            model = widget.get_model()
            disk_name = model[active_iter][0]  # Column 0: disk name
            disk_path = f"/dev/{disk_name}" if not disk_name.startswith("/dev/") else disk_name
            
            self.state.clone_source = disk_path
            logger.info(f"Clone source disk selected: {disk_path}")
        except Exception as e:
            logger.error(f"Error in clone source selection: {e}")

    def on_clonediskcombobox2_changed(self, widget):
        """Handle target disk selection (Page 3)."""
        try:
            active_iter = widget.get_active_iter()
            if active_iter is None:
                self.state.clone_target = ""
                return
            
            model = widget.get_model()
            disk_name = model[active_iter][0]  # Column 0: disk name
            disk_path = f"/dev/{disk_name}" if not disk_name.startswith("/dev/") else disk_name
            
            self.state.clone_target = disk_path
            logger.info(f"Clone target disk selected: {disk_path}")
        except Exception as e:
            logger.error(f"Error in clone target selection: {e}")

    def on_cloneStartButton_clicked(self, widget):
        """Start disk-to-disk clone operation (Page 3)."""
        try:
            source = self.state.clone_source
            target = self.state.clone_target
            
            # Validation
            if not source or not target:
                self._show_error("Please select both source and target disks")
                return
            
            if source == target:
                self._show_error("Source and target disks cannot be the same")
                return
            
            # Get disk sizes
            source_size = DiskManager.get_disk_size(source)
            target_size = DiskManager.get_disk_size(target)
            
            if source_size <= 0 or target_size <= 0:
                self._show_error("Could not determine disk sizes")
                return
            
            # **CRITICAL: Target must be >= Source**
            if target_size < source_size:
                self._show_error(
                    f"Target disk is too small!\n\n"
                    f"Source: {self._format_bytes(source_size)}\n"
                    f"Target: {self._format_bytes(target_size)}\n\n"
                    f"Target must be >= Source"
                )
                return
            
            # Show confirmation dialog
            dialog = Gtk.MessageDialog(
                parent=self.window,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Confirm Disk Clone"
            )
            dialog.format_secondary_text(
                f"You are about to clone:\n\n"
                f"Source: {source} ({self._format_bytes(source_size)})\n"
                f"Target: {target} ({self._format_bytes(target_size)})\n\n"
                f"All data on {target} will be OVERWRITTEN.\n"
                f"This cannot be undone. Continue?"
            )
            
            response = dialog.run()
            dialog.destroy()
            
            if response != Gtk.ResponseType.YES:
                logger.info("Clone operation cancelled by user")
                return
            
            # Get options
            block_size = self.state.clone_block_size
            disable_automount = self.state.clone_disable_automount
            unmount_and_remount = self.state.clone_unmount_and_remount
            
            # Disable buttons during operation
            widget.set_sensitive(False)
            self.state.operation_cancelled = False
            self.state.cancel_event = threading.Event()
            
            # Start operation in background thread
            thread = threading.Thread(
                target=self._clone_disk_worker,
                args=(source, target, block_size, disable_automount, unmount_and_remount),
                daemon=True
            )
            thread.start()
            
        except Exception as e:
            logger.error(f"Error starting clone: {e}")
            self._show_error(f"Error: {e}")
            widget.set_sensitive(True)

    def on_cloneStopButton_clicked(self, widget):
        """Stop clone operation (Page 3)."""
        self.state.operation_cancelled = True
        if self.state.cancel_event:
            self.state.cancel_event.set()
        logger.info("Clone operation cancelled by user")

    def _clone_disk_worker(self, source: str, target: str, block_size: str, 
                        disable_automount: bool, unmount_and_remount: bool):
        """Worker thread for disk clone operation."""
        try:
            # Handle mount options
            stopped_services = []
            killed_pids = []
            
            if disable_automount:
                stopped_services, _ = MountManager.stop_automount_services()
                killed_pids, _ = MountManager.kill_automount_processes()
            
            if unmount_and_remount:
                partitions_info, _ = MountManager.unmount_partitions_for_disk(target)
                self.state.mounted_partitions = partitions_info
            
            # Progress callback
            def progress_callback(bytes_done: int, total_bytes: int, status: str):
                GLib.idle_add(self._update_clone_progress, bytes_done, total_bytes, status)
            
            # Perform clone
            handler = DiskOperationHandler(
                source=source,
                destination=target,
                block_size=block_size,
                operation_type=OperationType.CLONE,
                progress_callback=progress_callback,
                cancel_event=self.state.cancel_event
            )
            
            success, message = handler.execute()
            
            # Update UI
            GLib.idle_add(self._finalize_clone, success, message, stopped_services, 
                        unmount_and_remount)
            
        except Exception as e:
            logger.error(f"Clone worker error: {e}")
            GLib.idle_add(self._show_error, f"Clone failed: {e}")
        finally:
            # Re-enable button
            GLib.idle_add(lambda: self.builder.get_object("cloneStartButton").set_sensitive(True))

    def _update_clone_progress(self, bytes_done: int, total_bytes: int, status: str):
        """Update clone progress bar and label."""
        try:
            if not self.clone_progress_bar or not self.clone_progress_label:
                logger.warning("Clone progress widgets not initialized")
                return False
            
            if total_bytes > 0:
                fraction = bytes_done / total_bytes
                percentage = fraction * 100
            else:
                fraction = 0
                percentage = 0
            
            # Format bytes for display
            bytes_str = self._format_bytes(bytes_done)
            total_str = self._format_bytes(total_bytes)
            
            # Update progress bar
            self.clone_progress_bar.set_fraction(fraction)
            self.clone_progress_bar.set_text(
                f"{status}: {percentage:.1f}% ({bytes_str} / {total_str})"
            )
            
            # Update label
            self.clone_progress_label.set_text(f"{percentage:.1f}%")
            
            logger.debug(f"Clone progress: {percentage:.1f}% ({bytes_str} / {total_str})")
            
            return False
        except Exception as e:
            logger.error(f"Error updating clone progress: {e}", exc_info=True)
            return False

    def _finalize_clone(self, success: bool, message: str, stopped_services: list, 
                        unmount_and_remount: bool):
        """Finalize clone operation and restore system state."""
        try:
            # Restart automount services if they were stopped
            if stopped_services:
                MountManager.start_automount_services(stopped_services)
            
            # Remount partitions if they were unmounted
            if unmount_and_remount and self.state.mounted_partitions:
                for partition in self.state.mounted_partitions:
                    if partition.mountpoint:
                        try:
                            os.makedirs(partition.mountpoint, exist_ok=True)
                            subprocess.run(
                                ["sudo", "mount", partition.path, partition.mountpoint],
                                capture_output=True,
                                timeout=10,
                                check=False
                            )
                            logger.info(f"Remounted {partition.path} at {partition.mountpoint}")
                        except Exception as e:
                            logger.warning(f"Failed to remount {partition.path}: {e}")
            
            # Show result
            if success:
                self._show_info("Clone Complete", f"Disk clone completed successfully!\n\n{message}")
            else:
                self._show_error(f"Clone failed: {message}")
            
            # Reset state
            self.state.reset_mount_state()
            
        except Exception as e:
            logger.error(f"Error finalizing clone: {e}")
            self._show_error(f"Error finalizing clone: {e}")
        
        
        
        
        
    
    
#======================================================
    
   

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
            self.state.clone_source = f"/dev/{disk_name}" if disk_name != "Select Disk" else ""
            logger.info(f"Clone source disk selected: {self.state.clone_source}")

    def on_clone_target_selected(self, widget):
        """Handler for clone target disk selection."""
        active_iter = widget.get_active_iter()
        if active_iter is not None:
            model = widget.get_model()
            disk_name = model[active_iter][0]
            self.state.clone_target = f"/dev/{disk_name}" if disk_name != "Select Disk" else ""
            logger.info(f"Clone target disk selected: {self.state.clone_target}")

    def on_clone_block_size_changed(self, widget):
        """Handler for block size selection in clone tab."""
        active_iter = widget.get_active_iter()
        if active_iter is not None:
            model = widget.get_model()
            block_size = model[active_iter][0]
            self.state.clone_block_size = block_size
            logger.info(f"Clone block size changed to: {block_size}")

    def on_clone_automount_toggled(self, widget):
        """Handler for disable automount toggle in clone tab."""
        self.state.clone_disable_automount = widget.get_active()
        status = "disabled" if self.state.clone_disable_automount else "enabled"
        logger.info(f"Clone automount {status}")

    def on_clone_remount_toggled(self, widget):
        """Handler for unmount and remount toggle in clone tab."""
        self.state.clone_unmount_and_remount = widget.get_active()
        status = "enabled" if self.state.clone_unmount_and_remount else "disabled"
        logger.info(f"Clone remount {status}")

    def on_refresh_disks_clone(self, widget):
        """Handler for refreshing disk list in clone tab."""
        logger.info("Refresh disks button clicked (clone tab)")
        # Implementation would refresh the disk list
        pass

    def on_clone_disk_clicked(self, widget):
        """Handler for initiating clone operation."""
        logger.info("Clone disk button clicked")
        if not self.state.clone_source or not self.state.clone_target:
            self.on_generalWarningError("Please select both source and target disks")
            return
        if self.state.clone_source == self.state.clone_target:
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
                f"You are about to clone disk:\n\n{self.state.clone_source}\n\n"
                f"to:\n\n{self.state.clone_target}\n\nWould you like to proceed?"
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
        
        self.clone_operation_in_progress = True
        self.clone_dialog = clone_dialog
        
        
        # Perform the clone operation
        if self.state.clone_disable_automount:
            # Stop automount services
            logger.info("Stopping automount services for clone operation")
        
        # Start clone thread
        logger.info(f"Starting clone from {self.state.clone_source} to {self.state.clone_target}")
        # Implementation would perform the actual clone
        self._start_operation(OperationType.CLONE)




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

    
        # Step 1: Discover partitions (needed for unmounting)
        if self.state.unmount_and_remount or self.state.disable_automount:
            self.state.discovered_partitions = DiskManager.discover_partitions(
                self.state.selected_disk
            )
            logger.info(f"Discovered {len(self.state.discovered_partitions)} partitions")
        
        # Step 2: Unmount partitions if requested
        if self.state.unmount_and_remount and self.state.discovered_partitions:
            logger.info(f"Unmounting partitions on {self.state.selected_disk}...")
            success = MountManager.unmount_partitions(self.state.discovered_partitions)
            
            # If normal unmount fails, try force unmount
            if not success:
                logger.warning("Normal unmount failed, attempting force unmount...")
                MountManager.force_unmount_partitions(self.state.discovered_partitions)
        
        # Step 3: Disable automount if requested
        if self.state.disable_automount:
            logger.info("Stopping automount services...")
            stopped_services, success = MountManager.stop_automount_services()
            self.state.stopped_services = stopped_services
            
            if stopped_services:
                logger.info(f"Stopped services: {', '.join(stopped_services)}")
            
            # Also kill automount processes
            killed_pids, success = MountManager.kill_automount_processes()
            if killed_pids:
                logger.info(f"Killed processes: {', '.join(killed_pids)}")


            
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
            

            success, message = disk_handler.execute()


            
            if success and not self.operation_stopped:
                logger.info("Read operation completed. Starting verification...")
                
                with open(self.state.image_path, 'rb') as f:
                    f.seek(0, 2)
                    self.state.image_size = f.tell()
    


                # Run verification
                if self._verify_operation():
                    self.progress_bar.set_fraction(0.0)
                    GLib.idle_add(
                        lambda: self._show_status(
                            f"Successfully read & verified {self.state.selected_disk} to {self.state.image_path}",
                            True
                        )
                    )
                else:
                    self.progress_bar.set_fraction(0.0)
                    logger.error("Verification failed after successful read")
                    # Error message already shown by _verify_operation()
            
            elif not success:
                error_msg = message
                GLib.idle_add(lambda msg=error_msg: self._show_error(f"Read operation failed: {msg}"))
            
            logger.info("Read operation completed")
        
        except Exception as e:
            logger.error(f"Read operation failed: {e}", exc_info=True)
            error_msg = str(e)
            GLib.idle_add(lambda msg=error_msg: self._show_error(f"Read operation failed: {msg}"))
        
        finally:
            self.is_operating = False
            # Remount partitions if they were unmounted
            if self.state.unmount_and_remount and self.state.discovered_partitions:
                logger.info("Remounting partitions...")
                success = MountManager.remount_partitions(self.state.discovered_partitions)
                if success:
                    logger.info("Successfully remounted partitions")
                else:
                    logger.warning("Some partitions failed to remount")
                self.state.discovered_partitions = []
            
            # Restart automount services if they were stopped
            if self.state.disable_automount and self.state.stopped_services:
                logger.info("Restarting automount services...")
                success = MountManager.start_automount_services(self.state.stopped_services)
                if success:
                    logger.info("Successfully restarted services")
                else:
                    logger.warning("Some services failed to restart")
                self.state.stopped_services = []

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
            
            success, message = disk_handler.execute()
            
            if success and not self.operation_stopped:
                logger.info("Write operation completed. Starting verification...")
                

                 # Capture image size for verification
                with open(self.state.image_path, 'rb') as f:
                    f.seek(0, 2)
                    self.state.image_size = f.tell()


                # Run verification
                if self._verify_operation():
                    self.progress_bar.set_fraction(0.0)
                    GLib.idle_add(
                        lambda: self._show_status(
                            f"Successfully wrote & verified {self.state.image_path} to {self.state.selected_disk}",
                            True
                        )
                    )
                else:
                    self.progress_bar.set_fraction(0.0)
                    logger.error("Verification failed after successful write")
                    # Error message already shown by _verify_operation()
            
            elif not success:
                error_msg = message
                GLib.idle_add(lambda msg=error_msg: self._show_error(f"Write operation failed: {msg}"))
            
            logger.info("Write operation completed")
        
        except Exception as e:
            logger.error(f"Write operation failed: {e}", exc_info=True)
            GLib.idle_add(lambda msg=str(e): self._show_error(f"Write operation failed: {msg}"))
        
        finally:
            self.is_operating = False
            # Remount partitions if they were unmounted
            if self.state.unmount_and_remount and self.state.discovered_partitions:
                logger.info("Remounting partitions...")
                success = MountManager.remount_partitions(self.state.discovered_partitions)
                if success:
                    logger.info("Successfully remounted partitions")
                else:
                    logger.warning("Some partitions failed to remount")
                self.state.discovered_partitions = []
            
            # Restart automount services if they were stopped
            if self.state.disable_automount and self.state.stopped_services:
                logger.info("Restarting automount services...")
                success = MountManager.start_automount_services(self.state.stopped_services)
                if success:
                    logger.info("Successfully restarted services")
                else:
                    logger.warning("Some services failed to restart")
                self.state.stopped_services = []

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
            
            success, message = disk_handler.execute()
            
            if success and not self.operation_stopped:
                logger.info("Clone operation completed. Starting verification...")
                
                # Run verification
                if self._verify_clone_operation():
                    self.clone_progress_bar.set_fraction(0.0)
                    GLib.idle_add(
                        lambda: self._show_status(
                            f"Successfully cloned & verified {self.state.clone_source} to {self.state.clone_target}",
                            True
                        )
                    )
                else:
                    self.clone_progress_bar.set_fraction(0.0)
                    logger.error("Verification failed after successful clone")
                    # Error message already shown by _verify_operation()
            
            elif not success:
                error_msg = message
                GLib.idle_add(lambda msg=error_msg: self._show_error(f"Clone operation failed: {msg}"))
            
            logger.info("Clone operation completed")
        
        except Exception as e:
            logger.error(f"Clone operation failed: {e}", exc_info=True)
            GLib.idle_add(lambda msg=str(e): self._show_error(f"Clone operation failed: {msg}"))
        
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
                        lambda br=bytes_read: self._update_verify_progress(
                            br, total_size, f"Computing {algorithm}"
                        )
                    )
            hash_value = hasher.hexdigest()
            
            GLib.idle_add(
                lambda hv=hash_value, algo=algorithm: self._show_hash_result(
                    f"{algo} Hash of {disk_path}:\n\n{hv}",
                    algo
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
        hash_only = message.strip().split('\n')[-1]

        # Put hash result in output text box
        if self.entry_checksum_output:
            self.entry_checksum_output.set_text(hash_only)
        else:
            logger.error("entry_checksum_output widget not found")
        
        dialog = Gtk.MessageDialog(
            parent=self.window,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK
        )
        dialog.set_markup("Hash Computation Complete!")
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

    

    def _update_progress_ui(self, percentage: float, bytes_done: int, bytes_total: int) -> False:
        """Update GTK widgets (runs in main thread)."""
        try:
            if self.progress_bar:
                self.progress_bar.set_fraction(percentage / 100.0)
            
            if self.percentage_label:
                self.percentage_label.set_text(f"{percentage:.1f}%")
            
            return False
        except Exception as e:
            logger.error(f"Failed to update progress UI: {e}")
            return False
        
    
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
    #ensure_elevated_privileges()
    app = DiskImagerApp()
    app.run()


