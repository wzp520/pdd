"""打包入口：确保浏览器内核就绪后，启动本地控制台界面。

PyInstaller 以本文件为入口打包成 pddbot.exe。
"""
from pddbot import browser_setup, gui  # 导入 gui 即注册 @ui.page 路由

if __name__ in {"__main__", "__mp_main__"}:
    browser_setup.ensure_chromium()
    gui.run()
