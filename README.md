# pandas to_sql 事务维护工作区

生产代码来自 pandas v2.2.2 和 SQLAlchemy 2.0.35 的原始发行包；
这是对真实入库调用链的审查，不是模拟事务实现。源码的完整读取及校验入口见
SOURCE.md，输入、输出、样例和交付约定见 CONTRACT.md。
待维护说明位于 docs/to_sql-transactions.md；它代表下游现有操作口径。

先运行 python3 bootstrap.py --prepare，再运行 python3 bootstrap.py checks.py normal。
python3 bootstrap.py probe.py incident 会展示真实失败调用及最终数据库状态。
源码、observer、上游事务断言和原样例保留；本轮交付审查、维护说明及可重跑证据。
