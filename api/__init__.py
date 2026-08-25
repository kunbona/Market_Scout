"""api/ — Flask Blueprint 拆分包。

server.py 按域拆分的落点。共享响应层在 common.py (_ok/_err/_date_param/_today),
各域一个 Blueprint 文件, server.py 只负责注册。

拆分原则 (2026-08 重构):
- 一次一域一 commit, 行为零变化 (路由路径/响应体不动);
- 域内模块级状态 (job dict / lock) 随 Blueprint 一起搬, 不跨模块共享;
- 存储函数懒导入 (函数体内 from db.storage import ...), 避免启动时大 import 图。
"""
