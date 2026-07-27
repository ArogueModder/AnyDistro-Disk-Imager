from setuptools import setup, find_packages

setup(
    name="anydistro-disk-imager",
    version="0.1.0",
    description="Read/Write/Verify/Clone Disk Images on any Linux distribution",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="ArogueModder",
    url="https://github.com/ArogueModder/AnyDistro-Disk-Imager",
    license="GPL-3.0",
    packages=find_packages(),
    python_requires=">=3.6",
    install_requires=[
        "PyGObject>=3.42.0",
        "playsound>=1.2.2",
    ],
    package_data={
        "anydistro_disk_imager": [
            "resources/*",
        ],
    },
    entry_points={
        "console_scripts": [
            "anydistro-disk-imager=anydistro_disk_imager.main:main",
        ],
    },
)
