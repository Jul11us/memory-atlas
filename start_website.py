"""Double-click launcher: reuse the local server or start it and open a browser."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser


ROOT = Path(__file__).resolve().parent
PORT = 8765
URL = f"http://127.0.0.1:{PORT}"


def server_ready() -> bool:
    # Local requests must not be routed through a configured system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(URL + "/", timeout=1) as response:
            return response.status == 200 and response.headers.get(
                "Server", ""
            ).startswith("MemoryAtlas/")
    except (OSError, urllib.error.URLError):
        return False


def open_website() -> None:
    print(f"网站地址：{URL}", flush=True)
    if not webbrowser.open(URL):
        print("浏览器未自动打开，请复制上面的地址到浏览器。", flush=True)


def main() -> int:
    if server_ready():
        open_website()
        return 0

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            print(f"端口 {PORT} 已被其他程序占用，请关闭占用程序后重试。")
            return 1

    print("正在启动本地记忆网络…", flush=True)
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "memory_atlas.py"), "--port", str(PORT)],
        cwd=ROOT,
        env=environment,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                print("启动失败，请查看上面的错误信息。", flush=True)
                return 1
            if server_ready():
                print("启动成功。使用期间请保留此窗口；按 Ctrl+C 可停止服务。", flush=True)
                open_website()
                return process.wait()
            time.sleep(0.25)
        print("启动超时，请查看上面的错误信息后重试。", flush=True)
        return 1
    except KeyboardInterrupt:
        print("\n正在停止网站…", flush=True)
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as error:
        print(f"无法启动网站：{error}")
        sys.exit(1)
