"""
共享的抓取状态对象。server.py 读取，scheduler.py 写入。
独立模块避免循环 import。
"""
import threading

fetch_lock = threading.Lock()

# status: "idle" | "running" | "done" | "auto"
fetch_state: dict = {
    "status": "idle",
    "results": [],
    "ts": "",
    "auto_ts": "",      # 最近一次自动抓取完成时间
    "auto_task": "",    # 当前正在自动抓取的任务名
}
