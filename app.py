import gi
import subprocess
import time
import os
import sys
import threading
import asyncio
import fcntl
import signal
import re
import shutil
import hashlib
import errno
import json
import warnings
from pathlib import Path
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango
from gi.repository import Gdk
from gi.repository import GLib
from playsound import playsound
from gi.repository import GObject
#from verifier import verifier

SelectedDiskName = "none"
SelectedDiskSize = ""
SelectedImageFile = ""
SelectedDiskActualSize = ""
SelectedDiskMountPoints = []
SelectedBlockSize = ""
Verify = False
OptionDisableAutomounter = False
OptionUnmountandRemount = False
VerifyhasRunOnce = False
VerifyHashSelect1 = ""
VerifyHashSelect2 = ""
VerifySelectedImageFile = ""
VerifySelectedDisk = ""
VerifySelectedDiskActualSize = ""
VerifyHashOutput = ""
CloneDiskNameSource = ""
CloneDiskNameTarget = ""
CloneDiskNameSourceSize = ""
CloneDiskNameTargetSize = ""
CloneOptionBlockSize = ""
CloneOptionDisableAutomount = False
CloneOptionUnmountandRemount = False
CHUNK = 8192
AUTOMOUNT_SERVICES = ["autofs", "automount", "udisks2", "gvfs-daemon", "gvfs-metadata"]
AUTOMOUNT_PROCS = ["udiskie", "gvfs", "udisksd", "udisks2", "autofs", "automount"]


class MyApp:
    def __init__(self):
        
        #######################################PAGE1INIT####################################
        
        #Check if elevated
        self.elevate()
        #self.ensure_elevated()
        #warnings.filterwarnings("error")

        # Load the Glade file to construct the GUI
        self.builder = Gtk.Builder()
        self.builder.add_from_file("DiskImager.glade")

        # Retrieve the main window from Glade and set up the close event
        self.window = self.builder.get_object("MyMainWindow")
        self.window.connect("destroy", Gtk.main_quit)
        self.window.set_title("Linux Disk Imager")
        self.window.set_icon_from_file("DiskImager.ico")

        # Retrieve the disk information from system process
        resultdisk = subprocess.run(['lsblk', '-d', '-n', '-o', 'NAME'], capture_output=True, text=True)
        resultdisksize = subprocess.run(['lsblk', '-d', '-n', '-o', 'SIZE'], capture_output=True, text=True)
        resultsplittotalsize = []
        disksplitname = resultdisk.stdout.split()
        disksplitsize = resultdisksize.stdout.split()
        for n in disksplitname:
            tds = self.get_disk_size(n)
            tdf = self.format_bytes(tds)
            resultsplittotalsize.append(str(tdf))

        #disksplittotalsize = resultsplittotalsize.stdout.split()
        print("totalsize"+str(resultsplittotalsize))
        disksplittotalsize = str(resultsplittotalsize)
        print(disksplitname)
        # Retrieve the stuff from Glade and connect the click events

        #Read Button
        self.button = self.builder.get_object("readImageButton")
        self.button.connect("clicked", self.on_ReadButtonClickedBox)
        #Write Button
        self.button = self.builder.get_object("writeImageButton")
        self.button.connect("clicked", self.on_WriteButtonClickedBox)
        #Refresh Disk Button
        self.button = self.builder.get_object("refreshDiskButton")
        self.button.connect("clicked", self.on_refreshDiskButton_clicked)
        #Quit Button
        self.button = self.builder.get_object("quitButton")
        self.button.connect("clicked", self.on_quitButton_clicked)
        #Disk Select Combo Box
        self.combo_box = self.builder.get_object("diskSelectCombo")
        self.combo_box.connect("changed", self.on_diskSelectCombo_changed)
        self.listStore = self.builder.get_object("liststore1")
         
        #Disk Image Select Setup
        self.button = self.builder.get_object("openSaveImageButton")
        self.button.connect("clicked", self.on_openSaveImageButton_activate)
        self.entry = self.builder.get_object("imageFileText")
        self.entry.connect("preedit-changed", self.on_imageFileText_preedit_changed)
        #Progress Bar Setup
        self.progressBar = self.builder.get_object("imageProgressBar")
        #Text Label Progress Percentage
        self.textProgressImage = self.builder.get_object("diskImageProgressPercentageLabel")
        #Toggles for Options
        self.toggle = self.builder.get_object("toggleDisableAutomount")
        self.toggle.connect("toggled", self.on_toggleDisableAutomount_toggled)
        self.toggle = self.builder.get_object("toggleUnmountandRemount")
        self.toggle.connect("toggled", self.on_toggleUnmountandRemount_toggled)

        #Blocksize ComboBox Options
        self.combo_box2 = self.builder.get_object("selectblocksizeCombo")
        self.combo_box2.connect("changed", self.on_selectblocksizeCombo_changed)
        self.listStore2 = self.builder.get_object("liststore2")
        self.combo_box2.set_model(self.listStore2)
        self.combo_box2.set_active(0)
        renderers = self.combo_box2.get_cells()             # returns list of CellRenderer
        for r in renderers:
            r.props.alignment = Pango.Alignment.LEFT


        #Dialog Box warning for Read Image
        self.readDialogBox = self.builder.get_object("ReadDialogBox")
        self.readDialogLabel = self.builder.get_object("ReadDialogMessageLabel")
        #Dialog Box OK and Cancel Buttons
        self.button = self.builder.get_object("DialogButtonOK")
        self.button.connect("clicked", self.prepForCleanImage)
        self.button = self.builder.get_object("DialogButtonCancel")
        self.button.connect("clicked", self.on_DialogButtonCancel_clicked)

        #Dialog Box warning for Write Image
        self.writeDialogBox = self.builder.get_object("WriteDialogBox")
        self.writeDialogLabel = self.builder.get_object("WriteDialogMessageLabel")
        #Dialog Box Write OK and Cancel Buttons
        self.button = self.builder.get_object("DialogButtonOK1")
        self.button.connect("clicked", self.prepForCleanWrite)
        self.button = self.builder.get_object("DialogButtonCancel1")
        self.button.connect("clicked", self.on_DialogButtonCancel1_clicked)
        
        #General Warning Dialog Box
        self.GeneralDialogBox = self.builder.get_object("GeneralErrorWarning")
        self.GeneralDialogLabel = self.builder.get_object("GeneralWarningLabel")
        self.button = self.builder.get_object("GeneralWarningButton")
        self.button.connect("clicked", self.on_GeneralWarningErrorClose_clicked)

        # Add select disk to top of disk list in combo box
        self.listStore.append(['Select Disk', '     File System Size', 'Total Disk Size'])
        self.combo_box.set_model(self.listStore)

        # Add an extra renderer to the ComboBox
        renderer2 = Gtk.CellRendererText()
        renderer2.props.alignment = Pango.Alignment.LEFT
        self.combo_box.pack_start(renderer2, True)
        self.combo_box.add_attribute(renderer2, "text", 1)  # Second column
        renderer3 = Gtk.CellRendererText()
        renderer3.props.alignment = Pango.Alignment.LEFT
        self.combo_box.pack_start(renderer3, True)
        self.combo_box.add_attribute(renderer3, "text", 2)  # Second column
        renderers = self.combo_box.get_cells()             # returns list of CellRenderer
        for r in renderers:
            r.props.alignment = Pango.Alignment.LEFT

        # set the first selection active
        self.combo_box.set_active(0)
        self.color_toggle = False

        # Add process variable
        self.proc = None
        self.proc2 = None
        self.procClone = None
        self.buffer = ""
        self.buffer2 = ""

        # Add some variables for detecting and stopping system automount
        self.stopped_system_services = []
        self.killed_procs = []  # pids
        

        # regex to match dd progress like "12345678 bytes (12 MB, 12 MiB) copied, 1 s, 12 MB/s"
        self.bytes_re = re.compile(r"(\d+)\s+bytes")

        # add Disks to combo box
        newtotal = resultsplittotalsize
        for n, s, t in zip(disksplitname, disksplitsize, newtotal):
            self.listStore.append([n, '     ' + s, t])

    ######################################PAGE2INIT########################################

        #Connect buttons for verifier
        self.button = self.builder.get_object("generateChecksumDiskButton")
        self.button.connect("clicked", self.on_generateChecksumDiskButton_clicked)
        self.button = self.builder.get_object("generateChecksumImageButton")
        self.button.connect("clicked", self.on_generateChecksumImageButton_clicked)
        self.button = self.builder.get_object("refreshDiskButton1")
        self.button.connect("clicked", self.on_refreshDiskButton1_clicked)
        self.button = self.builder.get_object("openimageButton2")
        self.button.connect("clicked", self.on_openimageButton2_clicked)
        self.entry2 = self.builder.get_object("imageverifyentry")
        self.entry3 = self.builder.get_object("ChecksumOutputEntry")
        self.progressBar2 = self.builder.get_object("verifyProgressBar")
        
        #ComboBox for Disk Selection
        self.combo_box5 = self.builder.get_object("verifydiskcombobox")
        self.combo_box5.connect("changed", self.on_verifydiskcombobox_changed)
        self.combo_box5.set_model(self.listStore)
        renderer4 = Gtk.CellRendererText()
        renderer4.props.alignment = Pango.Alignment.LEFT
        self.combo_box5.pack_start(renderer4, True)
        self.combo_box5.add_attribute(renderer4, "text", 1)  # Second column
        renderer5 = Gtk.CellRendererText()
        renderer5.props.alignment = Pango.Alignment.LEFT
        self.combo_box5.pack_start(renderer5, True)
        self.combo_box5.add_attribute(renderer5, "text", 2)
        self.combo_box5.set_active(0) 
        
        
        #Combo Box HASH selectors
        self.combo_box3 = self.builder.get_object("checksumCombobox1")
        self.combo_box3.connect("changed", self.on_checksumCombobox1_changed)
        self.listStore3 = self.builder.get_object("liststore3")
        self.combo_box3.set_model(self.listStore3)
        self.combo_box3.set_active(0)

        self.combo_box4 = self.builder.get_object("checksumCombobox2")
        self.combo_box4.connect("changed", self.on_checksumCombobox2_changed)
        self.listStore3 = self.builder.get_object("liststore3")
        self.combo_box4.set_model(self.listStore3)
        self.combo_box4.set_active(0)

#######################################PAGE3INIT###########################################

        #Buttons for page 3 Clone Disk

        self.button = self.builder.get_object("CloneDiskButton")
        self.button.connect("clicked", self.CloneDiskDialogBoxstart)
        self.button = self.builder.get_object("QuitButton1")
        self.button.connect("clicked", self.on_QuitButton1_clicked)
        self.button = self.builder.get_object("refreshDiskButton2")
        self.button.connect("clicked", self.on_refreshDiskButton2_clicked)
        self.progressBar3 = self.builder.get_object("cloneprogressbar")
        self.textProgressImage2 = self.builder.get_object("CloneDiskProgressLabel")
        self.button = self.builder.get_object("DialogButtonOK3")
        self.button.connect("clicked", self.on_CloneDiskButton_clicked)
        self.button = self.builder.get_object("DialogButtonCancel3")
        self.button.connect("clicked", self.on_DialogButtonCancel3_clicked)


        #Dialog box to confirm
        self.CloneDialogBox = self.builder.get_object("CloneDiskDialogBox")
        self.CloneDialogLabel = self.builder.get_object("CloneDialogMessageLabel")
        #Dialog Box Write OK and Cancel Buttons
        self.button = self.builder.get_object("DialogButtonOK3")
        self.button.connect("clicked", self.on_CloneDiskButton_clicked)
        self.button = self.builder.get_object("DialogButtonCancel3")
        self.button.connect("clicked", self.on_DialogButtonCancel1_clicked)

        #ComboBoxes for disks selection
        self.combo_boxclone1 = self.builder.get_object("clonediskcombobox1")
        self.combo_boxclone1.connect("changed", self.on_clonediskcombobox1_changed)
        self.combo_boxclone1.set_model(self.listStore)
        renderer6 = Gtk.CellRendererText()
        renderer6.props.alignment = Pango.Alignment.LEFT
        self.combo_boxclone1.pack_start(renderer6, True)
        self.combo_boxclone1.add_attribute(renderer6, "text", 1)  # Second column
        renderer7 = Gtk.CellRendererText()
        renderer7.props.alignment = Pango.Alignment.LEFT
        self.combo_boxclone1.pack_start(renderer7, True)
        self.combo_boxclone1.add_attribute(renderer7, "text", 2)
        self.combo_boxclone1.set_active(0) 

        self.combo_boxclone2 = self.builder.get_object("clonediskcombobox2")
        self.combo_boxclone2.connect("changed", self.on_clonediskcombobox2_changed)
        self.combo_boxclone2.set_model(self.listStore)
        renderer8 = Gtk.CellRendererText()
        renderer8.props.alignment = Pango.Alignment.LEFT
        self.combo_boxclone2.pack_start(renderer8, True)
        self.combo_boxclone2.add_attribute(renderer8, "text", 1)  # Second column
        renderer9 = Gtk.CellRendererText()
        renderer9.props.alignment = Pango.Alignment.LEFT
        self.combo_boxclone2.pack_start(renderer9, True)
        self.combo_boxclone2.add_attribute(renderer9, "text", 2)
        self.combo_boxclone2.set_active(0) 
        self.combo_boxclone1.set_model(self.listStore)
        self.combo_boxclone2.set_model(self.listStore)

        #Page 3 Options

        self.toggle = self.builder.get_object("toggleDisableAutomount1")
        self.toggle.connect("toggled", self.on_toggleDisableAutomount1_toggled)
        self.toggle = self.builder.get_object("toggleUnmountandRemount1")
        self.toggle.connect("toggled", self.on_toggleUnmountandRemount1_toggled)

        self.combo_boxBS = self.builder.get_object("selectblocksizeCombo1")
        self.combo_boxBS.connect("changed", self.on_selectblocksizeCombo1_changed)
        self.listStore2 = self.builder.get_object("liststore2")
        self.combo_boxBS.set_model(self.listStore2)
        self.combo_boxBS.set_active(0)
        renderers = self.combo_boxBS.get_cells()             # returns list of CellRenderer
        for r in renderers:
            r.props.alignment = Pango.Alignment.LEFT


   
    #Turn off annoying GTK warnings 
    
    # Domains: 'Gtk', 'GLib', 'Gdk', etc. Levels: GLib.LogLevelFlags.LEVEL_MASK
    def _noop_log_handler(domain, level, message, user_data):
    # return True to stop further handlers
        return True    
    GLib.log_set_handler('GLib', GLib.LogLevelFlags.LEVEL_MASK, _noop_log_handler, None)
    GLib.log_set_handler('Gtk', GLib.LogLevelFlags.LEVEL_MASK, _noop_log_handler, None)
    GLib.log_set_handler('Gdk', GLib.LogLevelFlags.LEVEL_MASK, _noop_log_handler, None)
        
    #Elevate privilages in order to do disk stuff

    def elevate(self):
        if os.getuid() != 0:  # Check if not running as root
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)  # Relaunch with sudo

    def ensure_elevated(self):
        if os.geteuid() == 0:
            return True
    # Relaunch the same script with pkexec
        argv = [sys.executable] + sys.argv
        try:
            ret = subprocess.run(["pkexec"] + argv)
            return ret.returncode == 0
        except FileNotFoundError:
        # pkexec not available
            return False

        
    ######################################PAGE1###############################################

    #OPTIONS

    def on_toggleDisableAutomount_toggled(self, toggle):
        global OptionDisableAutomounter
        if toggle.get_active():
            OptionDisableAutomounter = True
            print("Disabled Automount")
        else:
            OptionDisableAutomounter = False
            print("Enabled Automount")

    def on_toggleUnmountandRemount_toggled(self, toggle):
        global OptionUnmountandRemount
        if toggle.get_active():
            OptionUnmountandRemount = True
            print("Enabled Mount/Remount")
        else:
            OptionUnmountandRemount = False
            print("Disabled Mount/Remount")
            
    
    def on_selectblocksizeCombo_changed(self, combo):
        active_text = combo.get_active_iter()
        if active_text is not None:
            model = combo.get_model()
            selectedBlockSize = model[active_text][0]
            global SelectedBlockSize
            SelectedBlockSize = selectedBlockSize
            print(SelectedBlockSize)
    
    #When the Selected disk is changed adding the selection to the global variables

    def on_diskSelectCombo_changed(self, combo):
        #Get active selection from combo box
        active_text = combo.get_active_iter()
        if active_text is not None:
            model = combo.get_model()
            selDiskName = model[active_text][:2]
            selDiskName2 = model[active_text][0]
            print("Selected: disk =%s" % (selDiskName))
        #Pass selected Disk to global Variable and print selection 
            global SelectedDiskName
            SelectedDiskName = ("/dev/")+selDiskName2

            print(SelectedDiskName)
        
    #Refreshing the listed disks in the ComboBox

    def on_refreshDiskButton_clicked(self, widget):
        print("refresh Disk Button")
        self.listStore.clear()
        self.listStore.append(['Select Disk', '     File System Size', 'Total Disk Size'])
        resultdisk = subprocess.run(['lsblk', '-d', '-n', '-o', 'NAME'], capture_output=True, text=True)
        resultdisksize = subprocess.run(['lsblk', '-d', '-n', '-o', 'SIZE'], capture_output=True, text=True)
        print(resultdisk.stdout.splitlines())
        print(resultdisksize.stdout.splitlines())
        disksplitname = resultdisk.stdout.split()
        disksplitsize = resultdisksize.stdout.split()
        resultsplittotalsize = []
        for n in disksplitname:
            tds = self.get_disk_size(n)
            tdf = self.format_bytes(tds)
            resultsplittotalsize.append(str(tdf))
        self.combo_box.set_active(0)

        newtotal = resultsplittotalsize
        for n, s, t in zip(disksplitname, disksplitsize, newtotal):
            self.listStore.append([n, '     ' + s, t])
    
    def format_bytes(self, n, base=1000, units=None):
        n = float(n)
        if units is None:
            units = ['B','KiB','MiB','GiB','TiB','PiB'] if base==1024 else ['B','K','M','G','T','P']
        for unit in units:
            if abs(n) < base or unit == units[-1]:
                return f"{n:.1f}{unit}"
            n /= base


    #The actual work of recording an Disk Image to Disk
    #Updating the GUI while system works

    def on_readImageButton_clicked(self):
        global SelectedDiskActualSize
        self.get_disk_size2()
        if self.proc:
            return
        diskCommand = ["dd", "if="+SelectedDiskName, "of="+SelectedImageFile, "bs="+SelectedBlockSize, "status=progress"]
        self.proc = subprocess.Popen(diskCommand, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1, text=True, preexec_fn=os.setsid)
        
        for fd in (self.proc.stdout, self.proc.stderr):
            fl = fcntl.fcntl(fd.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(fd.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)

        # Watch both stdout and stderr (dd typically writes progress to stderr)
        GLib.io_add_watch(self.proc.stderr, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_io)
        GLib.io_add_watch(self.proc.stdout, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_io)

        # Optionally watch for process exit
        GLib.timeout_add(500, self.check_proc)

    
        
    def on_io(self, source, condition):
        try:
            chunk = source.readline()
        except Exception:
            chunk = None

        if not chunk:
            # no data right now
            if condition & (GLib.IO_HUP | GLib.IO_ERR):
                return False
            return True

        # accumulate and parse lines
        self.buffer += chunk
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.handle_line(line.strip())
        return True
    
    #handle output of process and update GUI

    def handle_line(self, line):
        # parse bytes and update progress bar fraction 
        global SelectedDiskActualSize
        m = self.bytes_re.search(line)
        
        if m:
            bytes_copied = int(m.group(1))
            total_bytes = SelectedDiskActualSize  # actual size of disk in bytes
            fraction = min(1.0, bytes_copied / total_bytes)
            GLib.idle_add(self.progressBar.set_fraction, fraction)
            percentFraction = (str(int(fraction * 100))+"%")
            GLib.idle_add(self.textProgressImage.set_text, percentFraction)

        # also show progress in text
        GLib.idle_add(self.progressBar.set_text, line)
        
    #Check the read/write process and complete output and errors

    def check_proc(self):
        if self.proc and self.proc.poll() is not None:
            # process finished
            exit_code = self.proc.returncode
            GLib.idle_add(self.progressBar.set_fraction, 1.0)
            GLib.idle_add(self.textProgressImage.set_text, "100%")
            print(exit_code)
            if exit_code == 0:
                GLib.idle_add(self.progressBar.set_text, f"Operation Completed Successfully")
                self.on_start_verify()
            elif exit_code in errno.errorcode:
                new_error_name = errno.errorcode.get(exit_code)
                error_description = os.strerror(exit_code)
                textError = ("Error! Code: "+new_error_name+" "+error_description+" "+str(exit_code))
                GLib.idle_add(self.progressBar.set_text, textError)
            self.proc.stdout.close()
            self.proc.stderr.close()
            self.proc = None
            
            return False
        return True

    #Get the actual disk size in bytes instead of the size minus filesystem

    def get_disk_size2(self):
        global SelectedDiskName
        global SelectedDiskActualSize
        #Get path to actual disk and it's size even through virtualEnv
        name = Path(SelectedDiskName).name
        size_path = Path('/sys/block') / name / 'size'
        if not size_path.exists():
            raise FileNotFoundError(f"ERROR File not Found")
        sectors = int(size_path.read_text().strip())
        SelectedDiskActualSize = (sectors * 512)
        print(SelectedDiskActualSize)
        return   

    #Function to return the total size of a disk

    def get_disk_size(self, diskname):
        #Get path to actual disk and it's size even through virtualEnv
        name = Path(diskname).name
        size_path = Path('/sys/block') / name / 'size'
        if not size_path.exists():
            raise FileNotFoundError(f"ERROR File not Found")
        sectors = int(size_path.read_text().strip())
        totaldisksize = (sectors * 512)
        #print(totaldisksize)
        return totaldisksize  

    #On clicking the read image button to bring up confirmation dialog

    def on_ReadButtonClickedBox(self, button):
        print("ReadButtonClicked")
        if SelectedDiskName == "/dev/Select Disk" or SelectedImageFile == "" or SelectedDiskName == "Select Disk":
            self.on_generalWarningError("\nPlease select a disk\n\n or  \n\nspecify a file and directory\n")
        else:
        # set message text before showing dialog
            self.readDialogLabel.set_line_wrap(True)
            self.readDialogLabel.set_line_wrap_mode(Gtk.WrapMode.WORD)
            self.readDialogBox.set_title("Read Image Confirmation")
            self.readDialogLabel.set_text("You are about to copy an image of the disk located at: \n\n"+ SelectedDiskName +"\n\n to the directory and file at: \n\n"+SelectedImageFile+"\n \n  Would you like to proceed?")
            self.readDialogBox.set_transient_for(self.window)
            self.readDialogBox.set_modal(True)
            self.readDialogBox.run()

    #On clicking the write image button to bring up confirmation dialog

    def on_WriteButtonClickedBox(self, button):
        print("WriteButtonClicked")
        if SelectedDiskName == "/dev/Select Disk" or SelectedImageFile == "" or SelectedDiskName == "Select Disk":
            self.on_generalWarningError("\nPlease select a disk\n\n or  \n\nspecify a file and directory\n")
        else:
        
        # set message text before showing dialog
            self.writeDialogLabel.set_line_wrap(True)
            self.writeDialogLabel.set_line_wrap_mode(Gtk.WrapMode.WORD)
            self.writeDialogBox.set_title("Write Image Confirmation")
            self.writeDialogLabel.set_text("You are about to write an image of a disk located at: \n\n"+ SelectedImageFile +"\n\n to the disk at: \n\n"+SelectedDiskName+"\n \n  Would you like to proceed?")
            self.writeDialogBox.set_transient_for(self.window)
            self.writeDialogBox.set_modal(True)
            self.writeDialogBox.run()
        
    #OK and cancel buttons for Read/Write dialog boxes

    def on_DialogButtonOK_clicked(self, button):
        self.on_readImageButton_clicked()
        self.readDialogBox.hide()
    def on_DialogButtonCancel_clicked(self, button):
        self.readDialogBox.hide()
    def on_DialogButtonOK1_clicked(self, button):
        self.on_writeImageButton_clicked()
        self.writeDialogBox.hide()
    def on_DialogButtonCancel1_clicked(self, button):
        self.writeDialogBox.hide()
    
    #Actual Work of Writing Image

    def on_writeImageButton_clicked(self):

        #print("Write Button")
        global SelectedDiskActualSize
        self.get_disk_size2()
        if self.proc:
            return
        diskCommand = ["dd", "if="+SelectedImageFile, "of="+SelectedDiskName, "bs="+SelectedBlockSize, "status=progress"]
        self.proc = subprocess.Popen(diskCommand, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1, text=True, preexec_fn=os.setsid)
        
        for fd in (self.proc.stdout, self.proc.stderr):
            fl = fcntl.fcntl(fd.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(fd.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)

        # Watch both stdout and stderr (dd typically writes progress to stderr)
        GLib.io_add_watch(self.proc.stderr, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_io)
        GLib.io_add_watch(self.proc.stdout, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_io)

        # Optionally watch for process exit
        GLib.timeout_add(500, self.check_proc)

    #General Warning Dialog Box
    
    def on_generalWarningError(self, warnError):
        #print(str(warnError))
        self.GeneralDialogLabel.set_line_wrap(True)
        self.GeneralDialogLabel.set_line_wrap_mode(Gtk.WrapMode.WORD)
        self.GeneralDialogBox.set_title("Warning")
        self.GeneralDialogLabel.set_text(warnError)
        self.GeneralDialogBox.set_transient_for(self.window)
        self.GeneralDialogBox.set_modal(True)
        self.GeneralDialogBox.run()
    
    def on_GeneralWarningErrorClose_clicked(self, button):
        print("close warning")
        self.GeneralDialogBox.hide()

    #Prep for clean image creation and writing
    #Main function for doing READ IMAGE

    def prepForCleanImage(self, button):
        global SelectedDiskName
        global SelectedDiskMountPoints
        global OptionDisableAutomounter
        global OptionUnmountandRemount
        parts = self.discover_partitions()
        
        for p in parts:
            print(f"Found partition: {p['path']} mounted at {p.get('mountpoint')}")
        
        if OptionUnmountandRemount == True:
            self.unmount_partitions(parts)
        
        if OptionDisableAutomounter == True:
            self.stop_detected_system_services()
            self.kill_detected_processes_and_holders()

        
        if OptionUnmountandRemount == True:
            ok = self.verify_no_mounts(timeout=30, interval=1)
            if not ok:
                print("Attempting fuser -km and lazy unmounts...")
                subprocess.run(["fuser", "-km", str(SelectedDiskName)], check=False)
                for p in parts:
                    if p.get("mountpoint"):
                        subprocess.run(["umount", "-l", p["path"]], check=False)
                ok = self.verify_no_mounts(timeout=15, interval=1)

            if not ok:
                print("Warning: partitions still mounted. Proceeding may produce inconsistent image.")

        self.set_readonly(True)
        #self.PretendtoReadImage()
        self.on_readImageButton_clicked()
        self.readDialogBox.hide()

    def prepForCleanWrite(self, button):
        global SelectedDiskName
        global SelectedDiskMountPoints
        global OptionDisableAutomounter
        global OptionUnmountandRemount
        parts = self.discover_partitions()
        print("Starting Write")
        for p in parts:
            print(f"Found partition: {p['path']} mounted at {p.get('mountpoint')}")
        
        if OptionUnmountandRemount == True:
            self.unmount_partitions(parts)
        
        if OptionDisableAutomounter == True:
            self.stop_detected_system_services()
            self.kill_detected_processes_and_holders()

        
        if OptionUnmountandRemount == True:
            ok = self.verify_no_mounts(timeout=30, interval=1)
            if not ok:
                print("Attempting fuser -km and lazy unmounts...")
                subprocess.run(["fuser", "-km", str(SelectedDiskName)], check=False)
                for p in parts:
                    if p.get("mountpoint"):
                        subprocess.run(["umount", "-l", p["path"]], check=False)
                ok = self.verify_no_mounts(timeout=15, interval=1)

            if not ok:
                print("Warning: partitions still mounted. Proceeding may produce inconsistent image.")

        #self.PretendtoWriteImage()
        self.on_writeImageButton_clicked()
        self.writeDialogBox.hide()

    #Finish READ/WRITE by doing the last things
    
    def finalizingReadWrite(self):
        global OptionUnmountandRemount
        global SelectedDiskName
        global SelectedImageFile
        global VerifyhasRunOnce
        print("final process")
        self.set_readonly(False)
        #self.progressBar.set_text("Verify Successful")
        if OptionUnmountandRemount == True:
            self.remount_partitions()
        global OptionDisableAutomounter
        if OptionDisableAutomounter == True:
            self.restart_stopped_services()
        VerifyhasRunOnce = False
    
    #Verify read/Write

    def _updateUI(self, read, total, source):
        def ui():
            global SelectedDiskActualSize
            SDAS = SelectedDiskActualSize
            GBtotal = ""
            if total:
                frac = min(1.0, read / total)
                #GBtotal = round(read / (1000**3), 2)
                byteTotal = self.format_bytes(read)
                frachalf = frac/2
                self.progressBar.set_fraction(frachalf)
                self.progressBar.set_text(f"Verifying: {source}: {byteTotal}")
                percentFraction = (str(int(frachalf * 100))+"%")
                GLib.idle_add(self.textProgressImage.set_text, percentFraction)
            else:
                frac2 = (min(1.0, read / SelectedDiskActualSize))/2
                frac3 = frac2 + 0.5
                self.progressBar.set_fraction(frac3)
                #GBtotal = round(read / (1000**3), 2)
                byteTotal = self.format_bytes(read)
                #GBnew = read / SelectedDiskActualSize
                self.progressBar.set_text(f"Verifying: {source}: {byteTotal}")
                percentFraction2 = (str(int(frac3 * 100))+"%")
                GLib.idle_add(self.textProgressImage.set_text, percentFraction2)
            return False
        GLib.idle_add(ui)

    def _hash_file(self, path, name):
        h = hashlib.sha256()
        total = os.path.getsize(path)
        read = 0
        dname = name
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
                self._updateUI(read, total, dname)
        return h.hexdigest()

    
    def on_start_verify(self):
        global SelectedDiskName
        global SelectedImageFile
        
        path2 = Path(SelectedImageFile)
        path = Path(SelectedDiskName)
        self.progressBar.set_fraction(0.0)
        self.progressBar.set_text("Starting Verify")

        def work():
            try:
                global VerifyhasRunOnce
                uhash = self._hash_file(path2, SelectedImageFile)
                VerifyhasRunOnce = True
                fhash = self._hash_file(path, SelectedDiskName)
                print(fhash)
                print(uhash)
                if fhash == uhash:
                    self.progressBar.set_text("Read/Write/Verify Successful")
                else:
                    self.progressBar.set_text("Verify Failed")
            finally:
                self.finalizingReadWrite()
                
                

        threading.Thread(target=work, daemon=True).start()


    #Discover the all partitions then append list to include only those from selected disk 
    
    def discover_partitions(self):
        global SelectedDiskName
        cmd = ['lsblk', '-J', '-o', 'NAME,KNAME,PATH,MOUNTPOINT,TYPE']
        res = subprocess.run(cmd, capture_output=True, text=True)
        info = json.loads(res.stdout)
        parts = []
        print("discovering partitions")
        
        for node in info.get("blockdevices", []):
            if Path(node.get("path", "")) == Path(SelectedDiskName):
                for child in node.get("children", []) or []:
                    if child.get("type") == "part":
                        parts.append({"path": child["path"], "mountpoint": child.get("mountpoint")})
                break
        print(parts)
        return parts

    #Unmount the partitions from the selected disk
    
    def unmount_partitions(self, parts):
        global SelectedDiskMountPoints
        for p in parts:
            mp = p.get("mountpoint")
            if mp:
                subprocess.run(["umount", p["path"]])
                SelectedDiskMountPoints.append({"path": p["path"], "mountpoint": mp})
                print(f"Unmounted {p['path']} from {mp}")
   
    #Set the disk to read only while reading to prevent errors or set RW after reading
    
    def set_readonly(self, ro=True):
        flag = "--setro" if ro else "--setrw"
        subprocess.run(["blockdev", flag, SelectedDiskName], check=False)

    #detecting automount process

    def detect_system_services(self):
        try:
            res = subprocess.run(["systemctl", "list-units", "--type=service", "--no-legend", "--all"],
                                 capture_output=True, text=True, check=False)
        except Exception:
            return []
        found = []
        for line in res.stdout.splitlines():
            name = line.split()[0]
            for cand in AUTOMOUNT_SERVICES:
                if name.startswith(cand):
                    found.append(name)
        return sorted(set(found))

    def detect_processes(self):
        try:
            res = subprocess.run(["ps", "axo", "pid:1,cmd"], capture_output=True, text=True, check=False)
        except Exception:
            return []
        found = []
        for line in res.stdout.splitlines()[1:]:
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            pid, cmd = parts
            for cand in AUTOMOUNT_PROCS:
                if cand in cmd:
                    found.append((pid, cmd))
        return found
    
    #Stop detected automount processes

    def stop_detected_system_services(self):
        svc = self.detect_system_services()
        for s in svc:
            res = subprocess.run(["systemctl", "stop", s], check=False)
            if res.returncode == 0:
                self.stopped_system_services.append(s)
                print(f"Stopped system service: {s}")

    def kill_detected_processes_and_holders(self):
        procs = self.detect_processes()
        pids = set()
        for pid, _ in procs:
            pids.add(pid)
        for pid in sorted(pids):
            try:
                subprocess.run(["kill", pid], check=False)
                time.sleep(0.1)
                res = subprocess.run(["kill", "-0", pid], check=False)
                if res.returncode == 0:
                    subprocess.run(["kill", "-9", pid], check=False)
                self.killed_procs.append(pid)
                print(f"Killed PID {pid}")
            except Exception:
                pass
    
    #Verify disk has been unmounted before proceeding

    def verify_no_mounts(self, timeout=30, interval=1):
        global SelectedDiskName
        end = time.time() + timeout
        while time.time() < end:
            res = subprocess.run(["lsblk", "-J", "-o", "NAME,PATH,MOUNTPOINT,TYPE"], capture_output=True, text=True, check=False)
            info = json.loads(res.stdout)
            mounted = False
            for node in info.get("blockdevices", []):
                if Path(node.get("path", "")) == SelectedDiskName:
                    for child in node.get("children", []) or []:
                        if child.get("type") == "part" and child.get("mountpoint"):
                            mounted = True
                            break
            if not mounted:
                print("No partitions mounted on", SelectedDiskName)
                return True
            time.sleep(interval)
        print("Timeout waiting for mounts to clear.")
        return False
    
    #Remount partitions back to where they were

    def remount_partitions(self):
        global SelectedDiskMountPoints
        for u in SelectedDiskMountPoints:
            Path(u["mountpoint"]).mkdir(parents=True, exist_ok=True)
            subprocess.run(["mount", u["path"], u["mountpoint"]], check=False)
            print(f"Remounted {u['path']} to {u['mountpoint']} (attempted)")

    #Restart detected automount processes that were stopped
    
    def restart_stopped_services(self):
        for s in self.stopped_system_services:
            subprocess.run(["systemctl", "start", s], check=False)
            print(f"Started system service: {s}")

    #Fake read/write for testing

    def PretendtoReadImage(self):
        print("pretending to read")
        self.get_disk_size2()
        self.on_start_verify()

    def PretendtoWriteImage(self):
        print("Pretending to Write Image")
        self.get_disk_size2()
        self.on_start_verify()


    #Quit the application
    
    def on_quitButton_clicked(self, widget):
        print("Quit Button")
        Gtk.main_quit()

    #Select directory and Image file and transfer to GUI and global 

    def on_imageFileText_preedit_changed(self, widget):
        text = widget.get_text()
        global SelectedImageFile 
        SelectedImageFile = text
        print("Entry text:", text)

    def on_openSaveImageButton_activate(self, widget):
        print("Open/Save As Button")
        dialog = Gtk.FileChooserDialog("Save/Open File",
                                        self.window,
                                        Gtk.FileChooserAction.SAVE,
                                        ("_Cancel", Gtk.ResponseType.CANCEL,
                                        "_Save/Open", Gtk.ResponseType.ACCEPT))
        
        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            filename = dialog.get_filename()
            print("File saved as:", filename)
            global SelectedImageFile
            SelectedImageFile = filename
            self.entry.set_text(SelectedImageFile)
        dialog.destroy()


#####################################PAGE2#############################################

    def on_generateChecksumDiskButton_clicked(self,button):
        print("Checksum Disk Button Clicked")
        global VerifySelectedDisk
        global VerifySelectedDiskActualSize
        path = Path(VerifySelectedDisk)
        VerifySelectedDiskActualSize = self.get_disk_size(VerifySelectedDisk)
        self.entry3.set_text("")
        self.progressBar2.set_text("")

        def work2():
            try:
                fhash = ""
                fhash = self.verify_hash_file(path, VerifySelectedDisk)

                
            finally:
                self.entry3.set_text(str(fhash))
                print("Hash Completed")
                self.progressBar2.set_text("Hash Complete")
                
                

        threading.Thread(target=work2, daemon=True).start()


    def on_generateChecksumImageButton_clicked(self,button):
        print("Checksum Image Button Clicked")
        global VerifySelectedImageFile
        path = Path(VerifySelectedImageFile)
        self.entry3.set_text("")
        self.progressBar2.set_text("")

        def work3():
            try:
                fhash = ""
                fhash = self.verify_hash_file2(path, VerifySelectedImageFile)

                
            finally:
                self.entry3.set_text(str(fhash))
                print("Hash Completed")
                self.progressBar2.set_text("Hash Complete")
                
                

        threading.Thread(target=work3, daemon=True).start()



    #Hash work here
    def verify_hash_file(self, path, name):
        global VerifyHashSelect1
        if VerifyHashSelect1 == "SHA256":
            h = hashlib.sha256()
        elif VerifyHashSelect1 == "SHA1":
            h = hashlib.sha1()
        elif VerifyHashSelect1 == "MD5":
            h = hashlib.md5()
        elif VerifyHashSelect1 == "SHA512":
            h = hashlib.sha512()
        total = os.path.getsize(path)
        read = 0
        dname = name
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
                self.verify_updateUI(read, total, dname)
        return h.hexdigest()
    
    def verify_hash_file2(self, path, name):
        global VerifyHashSelect2
        if VerifyHashSelect2 == "SHA256":
            h = hashlib.sha256()
        elif VerifyHashSelect2 == "SHA1":
            h = hashlib.sha1()
        elif VerifyHashSelect2 == "MD5":
            h = hashlib.md5()
        elif VerifyHashSelect2 == "SHA512":
            h = hashlib.sha512()
        total = os.path.getsize(path)
        read = 0
        dname = name
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
                self.verify_updateUI(read, total, dname)
        return h.hexdigest()
        

    def verify_updateUI(self, read, total, source):
        def ui2():
            global VerifySelectedDiskActualSize
            SDAS = VerifySelectedDiskActualSize

            if total:
                frac = min(1.0, read / total)
                byteTotal = self.format_bytes(read)
                percentFraction = (str(int(frac * 100))+"%")
                self.progressBar2.set_fraction(frac)
                self.progressBar2.set_text(f"Generating Hash: {source}: {byteTotal} {percentFraction}")
                
            else:
                frac2 = (min(1.0, read / SDAS))
                self.progressBar2.set_fraction(frac2)
                percentFraction2 = (str(int(frac2 * 100))+"%")
                byteTotal = self.format_bytes(read)
                self.progressBar2.set_text(f"Generating Hash: {source}: {byteTotal} {percentFraction2}")
                
            return False
        GLib.idle_add(ui2)
    

    #Combo Box Disk Selection

    def on_verifydiskcombobox_changed(self, combo):
        #Get active selection from combo box
        active_text = combo.get_active_iter()
        if active_text is not None:
            model = combo.get_model()
            selDiskName = model[active_text][:2]
            selDiskName2 = model[active_text][0]
            print("Selected: disk =%s" % (selDiskName))
        #Pass selected Disk to global Variable and print selection 
            global VerifySelectedDisk
            VerifySelectedDisk = ("/dev/")+selDiskName2

            print(VerifySelectedDisk)
        

    #Image File Selection for Verify
    
    def on_imageverifyentry_preedit_changed(self, widget):
        text = widget.get_text()
        global VerifySelectedImageFile 
        VerifySelectedImageFile = text
        print("Entry text:", text)

    def on_openimageButton2_clicked(self, button):
        print("open image")
        dialog = Gtk.FileChooserDialog("Open File",
                                        self.window,
                                        Gtk.FileChooserAction.SAVE,
                                        (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                        Gtk.STOCK_OPEN, Gtk.ResponseType.ACCEPT))
        dialog.set_select_multiple(False)
        
        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            filename = dialog.get_filename()
            print("File saved as:", filename)
            global VerifySelectedImageFile
            VerifySelectedImageFile = filename
            self.entry2.set_text(VerifySelectedImageFile)
        dialog.destroy()

    def on_refreshDiskButton1_clicked(self, button):
        print("refresh Disk Button")
        self.listStore.clear()
        self.listStore.append(['Select Disk', '     File System Size', 'Total Disk Size'])
        resultdisk = subprocess.run(['lsblk', '-d', '-n', '-o', 'NAME'], capture_output=True, text=True)
        resultdisksize = subprocess.run(['lsblk', '-d', '-n', '-o', 'SIZE'], capture_output=True, text=True)
        print(resultdisk.stdout.splitlines())
        print(resultdisksize.stdout.splitlines())
        disksplitname = resultdisk.stdout.split()
        disksplitsize = resultdisksize.stdout.split()
        resultsplittotalsize = []
        for n in disksplitname:
            tds = self.get_disk_size(n)
            tdf = self.format_bytes(tds)
            resultsplittotalsize.append(str(tdf))
        self.combo_box5.set_active(0)

        newtotal = resultsplittotalsize
        for n, s, t in zip(disksplitname, disksplitsize, newtotal):
            self.listStore.append([n, '     ' + s, t])

    #Combo Selection for Hash Type
    
    def on_checksumCombobox2_changed(self, combo):
        active_text = combo.get_active_iter()
        if active_text is not None:
            model = combo.get_model()
            selectedHash = model[active_text][0]
            global VerifyHashSelect2
            VerifyHashSelect2 = selectedHash
            print(VerifyHashSelect2)

    def on_checksumCombobox1_changed(self, combo):
        active_text = combo.get_active_iter()
        if active_text is not None:
            model = combo.get_model()
            selectedHash = model[active_text][0]
            global VerifyHashSelect1
            VerifyHashSelect1 = selectedHash
            print(VerifyHashSelect1)

#######################################PAGE3###########################################

    def on_refreshDiskButton2_clicked(self, button):
        print("Refresh button Clone Page")
        self.listStore.clear()
        self.listStore.append(['Select Disk', '     File System Size', 'Total Disk Size'])
        resultdisk = subprocess.run(['lsblk', '-d', '-n', '-o', 'NAME'], capture_output=True, text=True)
        resultdisksize = subprocess.run(['lsblk', '-d', '-n', '-o', 'SIZE'], capture_output=True, text=True)
        print(resultdisk.stdout.splitlines())
        print(resultdisksize.stdout.splitlines())
        disksplitname = resultdisk.stdout.split()
        disksplitsize = resultdisksize.stdout.split()
        resultsplittotalsize = []
        for n in disksplitname:
            tds = self.get_disk_size(n)
            tdf = self.format_bytes(tds)
            resultsplittotalsize.append(str(tdf))
        self.combo_boxclone1.set_active(0)
        self.combo_boxclone2.set_active(0)
        newtotal = resultsplittotalsize
        for n, s, t in zip(disksplitname, disksplitsize, newtotal):
            self.listStore.append([n, '     ' + s, t])

    #Check before writing

    def CloneDiskDialogBoxstart(self, button):
        print("CloneButtonClicked")
        if CloneDiskNameSource == "/dev/Select Disk" or CloneDiskNameTarget == "/dev/Select Disk" or CloneDiskNameSource == "Select Disk":
            self.on_generalWarningError("\nPlease select a source disk\n\n or  \n\na target disk\n")
        elif CloneDiskNameSource == CloneDiskNameTarget:
            self.on_generalWarningError("\nSource Disk and\n\n Target Disk  \n\ncannot be the same.\n")
        elif self.get_disk_size(CloneDiskNameTarget) <= self.get_disk_size(CloneDiskNameSource):
            self.on_generalWarningError("\nTarget Disk cannot \n\n be smaller than  \n\nSource Disk.\n")
        else:
        # set message text before showing dialog
            self.CloneDialogLabel.set_line_wrap(True)
            self.CloneDialogLabel.set_line_wrap_mode(Gtk.WrapMode.WORD)
            self.CloneDialogBox.set_title("Clone Disk Confirmation")
            self.CloneDialogLabel.set_text("You are about to clone a disk located at: \n\n"+ CloneDiskNameSource +"\n\n to the disk at: \n\n"+CloneDiskNameTarget+"\n \n  Would you like to proceed?")
            self.CloneDialogBox.set_transient_for(self.window)
            self.CloneDialogBox.set_modal(True)
            self.CloneDialogBox.run()

    #Start Writing process

    def on_CloneDiskButton_clicked(self, button):
        print("Clone Disk Start")
        global CloneDiskNameSource
        global SelectedDiskMountPoints
        global CloneOptionDisableAutomount
        global CloneOptionUnmountandRemount
        parts = self.discover_partitionsClone(CloneDiskNameSource)
        parts2 = self.discover_partitionsClone(CloneDiskNameTarget)
        print("Starting Write")
        for p in parts:
            print(f"Found partition: {p['path']} mounted at {p.get('mountpoint')}")
        
        if CloneOptionUnmountandRemount == True:
            self.unmount_partitions(parts)
            self.unmount_partitions(parts2)
        
        if CloneOptionDisableAutomount == True:
            self.stop_detected_system_services()
            self.kill_detected_processes_and_holders()

        
        if CloneOptionUnmountandRemount == True:
            ok = self.verify_no_mounts(timeout=30, interval=1)
            if not ok:
                print("Attempting fuser -km and lazy unmounts...")
                subprocess.run(["fuser", "-km", str(SelectedDiskName)], check=False)
                for p in parts:
                    if p.get("mountpoint"):
                        subprocess.run(["umount", "-l", p["path"]], check=False)
                ok = self.verify_no_mounts(timeout=15, interval=1)

            if not ok:
                print("Warning: partitions still mounted. Proceeding may produce inconsistent image.")

        #self.PretendtoWriteImage()
        self.CloneDialogBox.hide()
        self.on_CloneDiskButton2_clicked()
        

    def on_CloneDiskButton2_clicked(self):

        #sourceSize = self.get_disk_size(Path(CloneDiskNameSource))
        if self.procClone:
            return
        diskCommand = ["dd", "if="+CloneDiskNameSource, "of="+CloneDiskNameTarget, "bs="+CloneOptionBlockSize, "status=progress"]
        self.procClone = subprocess.Popen(diskCommand, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1, text=True, preexec_fn=os.setsid)
        
        for fd in (self.procClone.stdout, self.procClone.stderr):
            fl = fcntl.fcntl(fd.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(fd.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)

        # Watch both stdout and stderr (dd typically writes progress to stderr)
        GLib.io_add_watch(self.procClone.stderr, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_io2)
        GLib.io_add_watch(self.procClone.stdout, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_io2)

        # Optionally watch for process exit
        GLib.timeout_add(500, self.check_procClone)

    def check_procClone(self):
        if self.procClone and self.procClone.poll() is not None:
            # process finished
            exit_code = self.procClone.returncode
            GLib.idle_add(self.progressBar3.set_fraction, 1.0)
            GLib.idle_add(self.textProgressImage2.set_text, "100%")
            print(exit_code)
            if exit_code == 0:
                GLib.idle_add(self.progressBar3.set_text, f"Operation Completed Successfully")
                self.on_start_verify2()
            elif exit_code in errno.errorcode:
                new_error_name = errno.errorcode.get(exit_code)
                error_description = os.strerror(exit_code)
                textError = ("Error! Code: "+new_error_name+" "+error_description+" "+str(exit_code))
                GLib.idle_add(self.progressBar3.set_text, textError)
            self.procClone.stdout.close()
            self.procClone.stderr.close()
            self.procClone = None
            
            return False
        return True
    
    def on_io2(self, source, condition):
        try:
            chunk = source.readline()
        except Exception:
            chunk = None

        if not chunk:
            # no data right now
            if condition & (GLib.IO_HUP | GLib.IO_ERR):
                return False
            return True

        # accumulate and parse lines
        self.buffer += chunk
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.handle_line2(line.strip())
        return True
    
    #handle output of process and update GUI

    def handle_line2(self, line):
        # parse bytes and update progress bar fraction 
        cloneDiskSize = self.get_disk_size(Path(CloneDiskNameSource))
        m = self.bytes_re.search(line)
        
        if m:
            bytes_copied = int(m.group(1))
            total_bytes = cloneDiskSize  # actual size of disk in bytes
            fraction = min(1.0, bytes_copied / total_bytes)
            GLib.idle_add(self.progressBar3.set_fraction, fraction)
            percentFraction = (str(int(fraction * 100))+"%")
            GLib.idle_add(self.textProgressImage2.set_text, percentFraction)

        # also show progress in text
        GLib.idle_add(self.progressBar3.set_text, line)

    def on_QuitButton1_clicked(self, button):
        print("quit")
        Gtk.main_quit()

    #Start Verify of write

    def on_start_verify2(self):
        global CloneDiskNameSource
        global CloneDiskNameTarget
        global CloneDiskNameSourceSize
        global CloneDiskNameTargetSize
        path2 = Path(CloneDiskNameTarget)
        path = Path(CloneDiskNameSource)
        self.progressBar3.set_fraction(0.0)
        self.progressBar3.set_text("Starting Verify")
        CloneDiskNameSourceSize = self.get_disk_size(Path(CloneDiskNameSource))
        CloneDiskNameTargetSize = self.get_disk_size(Path(CloneDiskNameTarget))
        def work():
            try:
                global VerifyhasRunOnce
                #uhash = self._hash_file2(path2, CloneDiskNameTarget)
                uhash = self._hash_device_region(path2, CloneDiskNameSourceSize, CloneDiskNameTarget)
                VerifyhasRunOnce = True
                #fhash = self._hash_file2(path, CloneDiskNameSource)
                fhash = self._hash_device_region(path, CloneDiskNameSourceSize, CloneDiskNameSource)
                print(fhash)
                print(uhash)
                if fhash == uhash:
                    self.progressBar.set_text("Read/Write/Verify Successful")
                else:
                    self.progressBar.set_text("Verify Failed")
            finally:
                self.finalizingCloneWrite()
                
                

        threading.Thread(target=work, daemon=True).start()

    def _updateUI2(self, read, total, source):
        def ui2():
            SDAS = CloneDiskNameSourceSize
            SDAT = CloneDiskNameTargetSize
            GBtotal = ""
            if total:
                frac = min(1.0, read / total)
                byteTotal = self.format_bytes(read)
                frachalf = frac/2
                self.progressBar3.set_fraction(frachalf)
                self.progressBar3.set_text(f"Verifying: {source}: {byteTotal}")
                percentFraction = (str(int(frachalf * 100))+"%")
                GLib.idle_add(self.textProgressImage2.set_text, percentFraction)
            else:
                if VerifyhasRunOnce == True:
                    frac2 = ((min(1.0, read / SDAS))/2)
                else:
                    frac2 = ((min(1.0, read / SDAT))/2)
                
                frac3 = frac2
                if VerifyhasRunOnce == True:
                    frac3 = frac2 + 0.5
                self.progressBar3.set_fraction(frac3)
                byteTotal = self.format_bytes(read)
                self.progressBar3.set_text(f"Verifying: {source}: {byteTotal}")
                percentFraction2 = (str(int(frac3 * 100))+"%")
                GLib.idle_add(self.textProgressImage2.set_text, percentFraction2)
            return False
        GLib.idle_add(ui2)

    def _hash_file2(self, path, name):
        h = hashlib.sha256()
        total = os.path.getsize(CloneDiskNameSource)
        #total = 
        read = 0
        dname = name
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
                self._updateUI2(read, total, dname)
        return h.hexdigest()

    def _hash_device_region(self, path, length, name, chunk_size=4<<20):
        h = hashlib.sha256()
        read = 0
        dname = name
        with open(path, "rb") as dev:
            #dev.seek(offset)
            while read < length:
                to_read = min(chunk_size, length - read)
                data = dev.read(to_read)
                if not data:
                    raise IOError("Unexpected end of device read")
                h.update(data)
                read += len(data)
                self._updateUI2(read, length, dname)
        return h.hexdigest()

    def finalizingCloneWrite(self):
        global CloneOptionUnmountandRemount
        global CloneDiskNameSource
        global CloneDiskNameTarget
        global VerifyhasRunOnce
        print("final process")
        #self.set_readonly(False)
        #self.progressBar.set_text("Verify Successful")
        if CloneOptionUnmountandRemount == True:
            self.remount_partitions()
        global CloneOptionDisableAutomount
        if CloneOptionDisableAutomount == True:
            self.restart_stopped_services()
        VerifyhasRunOnce = False


    #Options

    def on_selectblocksizeCombo1_changed(self, combo):
        active_text = combo.get_active_iter()
        if active_text is not None:
            model = combo.get_model()
            selectedBlockSize = model[active_text][0]
            global CloneOptionBlockSize
            CloneOptionBlockSize = selectedBlockSize
            print(CloneOptionBlockSize)

    def on_toggleDisableAutomount1_toggled(self, toggle):
        global CloneOptionDisableAutomount
        if toggle.get_active():
            CloneOptionDisableAutomount = True
            print("Disabled Automount")
        else:
            CloneOptionDisableAutomount = False
            print("Enabled Automount")

    def on_toggleUnmountandRemount1_toggled(self, toggle):
        global CloneOptionUnmountandRemount
        if toggle.get_active():
            CloneOptionUnmountandRemount = True
            print("Enabled Mount/Remount")
        else:
            CloneOptionUnmountandRemount = False
            print("Disabled Mount/Remount")
            
    def discover_partitionsClone(self, name):
        cmd = ['lsblk', '-J', '-o', 'NAME,KNAME,PATH,MOUNTPOINT,TYPE']
        res = subprocess.run(cmd, capture_output=True, text=True)
        info = json.loads(res.stdout)
        parts = []
        print("discovering partitions")
        
        for node in info.get("blockdevices", []):
            if Path(node.get("path", "")) == Path(name):
                for child in node.get("children", []) or []:
                    if child.get("type") == "part":
                        parts.append({"path": child["path"], "mountpoint": child.get("mountpoint")})
                break
        print(parts)
        return parts    



    #Clone Disk Selection Combo Boxes

    def on_clonediskcombobox1_changed(self, combo):
        #Get active selection from combo box
        active_text = combo.get_active_iter()
        if active_text is not None:
            model = combo.get_model()
            selDiskName = model[active_text][:2]
            selDiskName2 = model[active_text][0]
            print("Selected: disk =%s" % (selDiskName))
        #Pass selected Disk to global Variable and print selection 
            global CloneDiskNameSource
            CloneDiskNameSource = ("/dev/")+selDiskName2

            print(CloneDiskNameSource)

    def on_clonediskcombobox2_changed(self, combo):
        #Get active selection from combo box
        active_text = combo.get_active_iter()
        if active_text is not None:
            model = combo.get_model()
            selDiskName = model[active_text][:2]
            selDiskName2 = model[active_text][0]
            print("Selected: disk =%s" % (selDiskName))
        #Pass selected Disk to global Variable and print selection 
            global CloneDiskNameTarget
            CloneDiskNameTarget = ("/dev/")+selDiskName2

            print(CloneDiskNameTarget)
        
    def on_DialogButtonCancel3_clicked(self, button):
        self.CloneDialogBox.hide()


    def run(self):
        self.window.show_all()
        Gtk.main()

if __name__ == "__main__":
    app = MyApp()
    app.run()