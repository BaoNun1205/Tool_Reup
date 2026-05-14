from setuptools import find_packages, setup

setup(
    name="auto-tiktok-editor",
    version="0.1.0",
    description="MVP auto TikTok video editor for product overlay workflows",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "yt-dlp[default]>=2024.8.6",
    ],
    entry_points={
        "console_scripts": [
            "auto-tiktok-editor=auto_tiktok_editor.cli:main",
            "auto-tiktok-telegram-bot=auto_tiktok_editor.telegram_worker:main",
            "auto-tiktok-telegram-web=auto_tiktok_editor.telegram_web_service:main",
        ]
    },
)
