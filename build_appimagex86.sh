#!/bin/bash

# build-appimage.sh - Build AnyDistro-Disk-Imager AppImage (x86_64 and aarch64)
# This script creates a self-contained AppImage for distribution

set -e

# ============================================================================
# Configuration
# ============================================================================

PROJECT_NAME="AnyDistro-Disk-Imager"
APP_ID="anydistro-disk-imager"
VERSION="0.1.0"  # Update this as needed

# Detect architecture
ARCH=$(uname -m)
if [ "$ARCH" != "x86_64" ] && [ "$ARCH" != "aarch64" ]; then
    echo "ERROR: Unsupported architecture: $ARCH"
    echo "Supported architectures: x86_64, aarch64"
    exit 1
fi

echo "Building AppImage for: $ARCH"

# ============================================================================
# Paths
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
APPDIR="$BUILD_DIR/${PROJECT_NAME}.AppDir"
DIST_DIR="$SCRIPT_DIR/dist"
PYTHON_VERSION="3.11"  # Match your pyenv Python version

# Create directories
mkdir -p "$BUILD_DIR"
mkdir -p "$DIST_DIR"
rm -rf "$APPDIR"  # Clean old AppDir

# ============================================================================
# Step 1: Download linuxdeploy and appimagetool
# ============================================================================

echo "=== Step 1: Downloading build tools for $ARCH ==="

LINUXDEPLOY_URL="https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-${ARCH}.AppImage"
APPIMAGETOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${ARCH}.AppImage"

LINUXDEPLOY="$BUILD_DIR/linuxdeploy-${ARCH}.AppImage"
APPIMAGETOOL="$BUILD_DIR/appimagetool-${ARCH}.AppImage"

# Download linuxdeploy
if [ ! -f "$LINUXDEPLOY" ]; then
    echo "Downloading linuxdeploy for $ARCH..."
    wget -q "$LINUXDEPLOY_URL" -O "$LINUXDEPLOY"
    chmod +x "$LINUXDEPLOY"
fi

# Download appimagetool
if [ ! -f "$APPIMAGETOOL" ]; then
    echo "Downloading appimagetool for $ARCH..."
    wget -q "$APPIMAGETOOL_URL" -O "$APPIMAGETOOL"
    chmod +x "$APPIMAGETOOL"
fi

# ============================================================================
# Step 2: Create AppDir structure
# ============================================================================

echo "=== Step 2: Creating AppDir structure ==="

mkdir -p "$APPDIR"/{usr/bin,usr/lib,usr/share/applications,usr/share/icons/hicolor/256x256/apps}

# ============================================================================
# Step 3: Copy application and dependencies
# ============================================================================

echo "=== Step 3: Installing application and dependencies ==="

# Find Python executable from pyenv
PYTHON_EXEC="$HOME/.pyenv/versions/${PYTHON_VERSION}.*/bin/python${PYTHON_VERSION}"
PYTHON_REAL=$(eval echo $PYTHON_EXEC | head -1)

if [ ! -f "$PYTHON_REAL" ]; then
    echo "ERROR: Python $PYTHON_VERSION not found in pyenv"
    echo "Available Python versions:"
    ls -la "$HOME/.pyenv/versions/" 2>/dev/null || echo "  (no pyenv versions found)"
    exit 1
fi

PYTHON_ROOT=$(dirname $(dirname "$PYTHON_REAL"))
echo "Using Python from: $PYTHON_ROOT"

# Copy Python executable and standard library
echo "Bundling Python interpreter..."
cp "$PYTHON_REAL" "$APPDIR/usr/bin/python"
chmod +x "$APPDIR/usr/bin/python"
ln -s python "$APPDIR/usr/bin/python3"
chmod +x "$APPDIR/usr/bin/python3" 
cp -r "$PYTHON_ROOT/lib/python${PYTHON_VERSION}" "$APPDIR/usr/lib/"

# Install application and dependencies into AppDir site-packages
SITE_PACKAGES="$APPDIR/usr/lib/python${PYTHON_VERSION}/site-packages"
mkdir -p "$SITE_PACKAGES"

echo "Installing application into AppDir..."
pip install \
    --target "$SITE_PACKAGES" \
    --no-deps \
    "$SCRIPT_DIR"

echo "Installing dependencies into AppDir..."
pip install \
    --target "$SITE_PACKAGES" \
    'PyGObject>=3.42.0' \
    'playsound>=1.2.2'

# Inside the build script, after pip install, remove the duplicate:
rm -rf "$APPDIR/usr/lib/python${PYTHON_VERSION}/site-packages/bin"



# Step 4: Create AppRun that handles noexec mounts
# ============================================================================

cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
set -e

APPDIR="$(dirname "$(readlink -f "${0}")")"

# Create temp directory and copy Python + libs
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

cp -r "${APPDIR}/usr" "$TMPDIR/"
chmod +x "$TMPDIR/usr/bin/python"

# Run from temp
export PYTHONHOME="$TMPDIR/usr"
export PYTHONPATH="$TMPDIR/usr/lib/python3.11/site-packages"
exec "$TMPDIR/usr/bin/python" -m anydistro_disk_imager.__main__ "$@"
EOF

chmod +x "$APPDIR/AppRun"



# ============================================================================
# Step 5: Copy desktop file and icon
# ============================================================================

echo "=== Step 5: Setting up desktop integration ==="

# Create desktop file if it doesn't exist
if [ ! -f "$SCRIPT_DIR/${APP_ID}.desktop" ]; then
    cat > "$APPDIR/usr/share/applications/${APP_ID}.desktop" << EOF
[Desktop Entry]
Type=Application
Name=AnyDistro Disk Imager
Comment=A simple disk imaging utility for any Linux distribution
Exec=${APP_ID}
Icon=${APP_ID}
Categories=Utility;System;
Terminal=false
StartupNotify=true
EOF
else
    cp "$SCRIPT_DIR/${APP_ID}.desktop" "$APPDIR/usr/share/applications/"
fi

# Copy icon if it exists
if [ -f "$SCRIPT_DIR/anydistro-disk-imager.png" ]; then
    cp "$SCRIPT_DIR/anydistro-disk-imager.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/${APP_ID}.png"
elif [ -f "$SCRIPT_DIR/DiskImager.png" ]; then
    cp "$SCRIPT_DIR/DiskImager.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/${APP_ID}.png"
elif [ -f "$SCRIPT_DIR/resources/DiskImager.png" ]; then
    cp "$SCRIPT_DIR/resources/DiskImager.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/${APP_ID}.png"
else
    echo "WARNING: Icon file not found, skipping icon"
fi



# ============================================================================
# Step 7: Build AppImage with linuxdeploy
# ============================================================================

echo "=== Step 7: Building AppImage with linuxdeploy ==="

cd "$BUILD_DIR"

# Use linuxdeploy to package the AppDir
"$LINUXDEPLOY" \
    --appdir="$APPDIR" \
    --output=appimage \
    --executable="$APPDIR/usr/bin/python3"

# ============================================================================
# Step 8: Move AppImage to dist directory
# ============================================================================

echo "=== Step 8: Finalizing AppImage ==="

# linuxdeploy converts hyphens in AppDir name to underscores in output
APPIMAGE_NAME="${PROJECT_NAME//-/_}-${ARCH}.AppImage"
APPIMAGE_SOURCE="$BUILD_DIR/$APPIMAGE_NAME"

if [ -f "$APPIMAGE_SOURCE" ]; then
    mv "$APPIMAGE_SOURCE" "$DIST_DIR/$APPIMAGE_NAME"
    chmod +x "$DIST_DIR/$APPIMAGE_NAME"
    
    # Print summary
    echo ""
    echo "✓ AppImage build successful!"
    echo ""
    echo "Output: $DIST_DIR/$APPIMAGE_NAME"
    echo "Size: $(du -h "$DIST_DIR/$APPIMAGE_NAME" | cut -f1)"
    echo "Architecture: $ARCH"
    echo ""
    echo "To test the AppImage:"
    echo "  $DIST_DIR/$APPIMAGE_NAME"
    echo ""
else
    echo "ERROR: AppImage not found at $APPIMAGE_SOURCE"
    echo "Found files:"
    ls -lh "$BUILD_DIR"/*.AppImage 2>/dev/null || echo "No AppImage files found"
    exit 1
fi

# ============================================================================
# Step 9: Verify AppImage
# ============================================================================

echo "=== Step 9: Verifying AppImage ==="

file "$DIST_DIR/$APPIMAGE_NAME"

if file "$DIST_DIR/$APPIMAGE_NAME" | grep -q "ELF.*executable"; then
    echo "✓ AppImage is a valid ELF executable"
else
    echo "WARNING: AppImage file type check failed"
fi

echo ""
echo "Build complete!"
