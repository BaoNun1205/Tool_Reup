from setuptools import find_packages, setup

setup(
    name="auto-tiktok-editor",
    version="0.1.0",
    description="TikTok Profile Manager",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "customtkinter>=5.2,<6",
        "playwright>=1.44,<2",
        "pywinauto>=0.6.8,<1",
        "yt-dlp[default]>=2024.8.6",
    ],
    entry_points={
        "console_scripts": [
            "auto-tiktok-editor=auto_tiktok_editor.cli:main",
        ]
    },
)
