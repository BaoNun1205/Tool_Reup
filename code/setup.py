from setuptools import find_packages, setup

setup(
    name="auto-tiktok-editor",
    version="4.1.0",
    description="TikTok Profile Manager",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "customtkinter>=5.2,<6",
        "backgroundremover",
        "onnxruntime-directml>=1.18,<2",
        "Pillow<12",
        "playwright>=1.44,<2",
        "pywinauto>=0.6.8,<1",
        "rembg==2.0.67",
        "uiautomator2>=3.2,<4",
        "yt-dlp[default]>=2024.8.6",
    ],
    entry_points={
        "console_scripts": [
            "auto-tiktok-editor=auto_tiktok_editor.cli:main",
        ]
    },
)
