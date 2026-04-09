from setuptools import find_packages, setup

setup(
    name="auto-tiktok-editor",
    version="0.1.0",
    description="MVP auto TikTok video editor for product overlay workflows",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    entry_points={
        "console_scripts": [
            "auto-tiktok-editor=auto_tiktok_editor.cli:main",
        ]
    },
)
