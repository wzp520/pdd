"""首次运行自动下载 Playwright Chromium 内核。

打包后浏览器内核放在 exe 旁的 ms-playwright 目录（见 config），用户第一次启动时
自动下载、之后复用，无需单独安装 Python 或 playwright。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _chromium_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = p.chromium.executable_path
        return bool(path) and Path(path).exists()
    except Exception:
        return False


def ensure_chromium(log=print) -> None:
    """确保 Chromium 已就绪，没有则下载。"""
    if _chromium_ready():
        return
    log("首次运行，正在下载浏览器内核（约 150MB，取决于网速，请耐心等待，勿关闭窗口）…")
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env

        driver = compute_driver_executable()
        cmd = list(driver) if isinstance(driver, (list, tuple)) else [driver]
        cmd += ["install", "chromium"]
        subprocess.run(cmd, env={**os.environ, **get_driver_env()}, check=True)
        log("浏览器内核下载完成 ✅")
    except Exception as e:
        log(f"浏览器内核下载失败：{e}")
        log("请联网后重试启动，或手动运行：playwright install chromium")
