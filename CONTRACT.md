# 分块写入事务审查工作区

这次维护针对 pandas v2.2.2 的 DataFrame.to_sql，经 SQLAlchemy 2.0.35 写 SQLite。
生产实现随固定版本发行包提供，SOURCE.md 和 source_manifest.py 索引原始源码；
docs/to_sql-transactions.md 是待维护的下游操作说明。不要将它当作正确性判定器。

## 可执行材料

使用 Python 3.9–3.12。首次准备：python3 bootstrap.py --prepare。
缺少固定核心版本时，bootstrap.py 在 .review-venv 中安装 requirements.txt；
首次准备需要取得公开依赖，已准备的环境执行、查看源码及重跑证据不需要网络。
无需账户、外部数据或数据库服务。

原正常回归：python3 bootstrap.py checks.py normal。
它执行保留的上游事务断言及公开 to_sql 正常分块写入，核对表结构、行和提交轨迹。
正常样例写入 [1,2,3,4]，返回 4，旧值 90 和 legacy 列及索引均保留。
公开事故：python3 bootstrap.py checks.py incident。
该命令以“失败不改数据库”的现有操作承诺作断言；原版应报 TX_ATOMICITY_MISMATCH。
不要修复生产实现或将此事故命令改为通过；审查须解释承诺是否成立。

观察入口：
    python3 bootstrap.py probe.py normal
    python3 bootstrap.py probe.py incident
    python3 bootstrap.py probe.py replace
    python3 bootstrap.py probe.py --spec case.json

case.json 是对象，全部字段：
{"if_exists":"append","owner":"connection","catch":false,"begin":"legacy","values":[1,2,-3,4],"chunksize":2}

if_exists 仅 append/replace；owner 仅 engine/connection；catch 为布尔，
仅 connection 可为 true；begin 为 legacy/explicit；values 为非空整数数组，
chunksize 为正整数。输入不合法时入口拒绝。业务审查只使用 chunksize=2。
这只是可重跑证据入口，不能将其开发成新的入库工具。

每次创建独立命名内存库，锚连接维持库生命周期；写入使用 NullPool，
写连接退出并 dispose 后，再用独立 sqlite3 连接读取结构及行。
sqlite3 只用于建立样本、观测和事务配置，数据导入一律调用公开 DataFrame.to_sql。
本题不涉及进程重开或持久文件恢复；“独立连接”不等于复用写连接读未提交结果。

初始表固定为：
CREATE TABLE readings (value INTEGER NOT NULL CHECK(value>=0), legacy TEXT NOT NULL DEFAULT 'old');
CREATE INDEX readings_value ON readings(value);
INSERT INTO readings(value) VALUES (90);
写入 DataFrame 只有 value 列，dtype=object，to_sql 显式传 BigInteger、index=False、
method=None。正常 values 为 [1,2,3,4]；后块约束错误为 [1,2,-3,4]；
后块整数溢出为 [1,2,9223372036854775808,4]。
replace 会重建目标表，不能假定新表继承旧约束；不扩展类型推断或插入回调。

engine 表示直接传 Engine；connection 表示传 engine.begin() 的连接。
catch=false 时异常逃出最外层事务作用域；catch=true 时在该作用域内捕获后正常退出。
legacy 固定 sqlite3 的传统事务控制；explicit 使用 SQLAlchemy 文档中的显式 BEGIN 配置。
配置、异常捕获位置和所有 SQL 都有可见轨迹，不可根据 SQLAlchemy 的日志文字推测物理事务。
sqlite 轨迹中的 active_before 是执行该条语句前的 sqlite3.in_transaction。
sqlalchemy 事件同时记录该时刻的逻辑事务和 sqlite3 状态。

probe 输出一个 JSON 对象：spec、versions、returned、errors、trace、before、after。
errors 项有 where、type、message、cause_type。trace 按发生顺序保留
sqlite SQL、SQLAlchemy 事务事件和调用边界；前后快照包含 sqlite_master 对象、
PRAGMA table_info 的列结构和 SELECT * ORDER BY value 的完整行。
UUID 不进入输出。观察命令成功取得证据即退出 0；数据库异常保存在 errors 中。
checks.py incident 则按承诺作断言，可稳定失败。

## 维护交付约定

REVIEW.md 追踪 DataFrame.to_sql 到实际驱动事务的调用链，引用原始模块及行号。
区分 pandas 自有事务的义务、调用者外层事务的义务及 SQLite 驱动边界；
逐项记录触发输入、独立期望、实际结构与旧新数据、发布影响和最小兼容修复取舍。
不能只列现象、复述公开事故或把所有差异都归责于 pandas。
建议须解释异常转发、事务所有权及 DDL/DML 边界，不能无条件回滚调用者工作，
也不能以“加一个事务上下文”作为未核实的原子性承诺。

对照范围是 append、replace，Engine、外层 Connection，异常逃逸、内部捕获后提交，
以及 legacy 与显式 BEGIN。用仅改变一个因素的案例隔离结论，并保留成功提交和
真正完整回滚的正例。无需穷举无关输入；替表失败至少核对旧列、索引、旧行和新行，
不能仅比较行数或只检查异常类型。

修订 docs/to_sql-transactions.md，使使用者能判断现有调用是否保留部分数据、
替表能否恢复旧表、何时必须由调用者处理失败；将现状、责任及兼容修复建议明确区分。
本轮不实现生产修复、不开发通用原子替表框架，不审查其他数据库、原生 sqlite3
to_sql 适配器或自定义 method，也不增加并发、崩溃恢复及性能目标。

review_evidence.py run 直接调用 probe.run_case，生成 evidence.json；
每个案例保存完整输入、原始观察、独立期望和对应的报告定位。
期望须由输入与事务责任独立给出，不能将实际结果反填为期望。
review_evidence.py verify 重跑证据、核对保存的实际结果及报告引用，并保留正常回归；
对已确认的实现缺陷，验证应确认差异仍能复现，不能要求缺陷消失才算审查完成。
review_evidence.py demo 展示成功、异常及替表的真实结构和数据差异。
以上入口均通过 python3 bootstrap.py SCRIPT COMMAND 调用。
交付时运行清单的 verify、demo 分别改为对应的 review_evidence.py verify、demo；
setup 保持固定环境准备。证据格式的其余组织方式自行选择。
