#!/usr/bin/env python3
"""
AnyDistro Disk Imager - A GTK3-based disk imaging utility for Linux.
Features: Read/Write disk images, verify operations, clone disks.
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
        units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
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
                logger.debug(f"Process {proc} not found or error: {e
