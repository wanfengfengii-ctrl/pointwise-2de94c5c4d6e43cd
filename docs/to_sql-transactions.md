# 数据任务的 to_sql 事务说明（修订版）

适用：DataFrame 通过 SQLAlchemy 2.0.35 写 SQLite（pandas 固定 2.2.2）；每两行一个块，`method=None`。
本说明区分三类内容：**【现状】**固定版本可复现的实际行为；**【责任】**事务归属与失败处置权；
**【建议】**兼容修复方向（本轮未实现，需按发布流程评估）。带 E 编号的结论对应 REVIEW.md 与 evidence.json 案例。

> 修订背景：原说明第 1–4 条的“失败必回滚、替表可恢复、begin 即物理事务”口径经固定版本核验不成立。
> 公开事故命令 `python3 bootstrap.py checks.py incident` 至今稳定报 `TX_ATOMICITY_MISMATCH`，
> 即“失败不改数据库”的旧承诺与实现不符。

## 1. 现状：失败后数据库到底是什么状态

### 1.1 传 Engine（pandas 自建连接与事务）

- 【现状】后块报错（约束错误或整数溢出）时，**此前已成功的分块会被提交保留**，异常同时抛给调用者。
  驱动轨迹为 `BEGIN → 成功块 INSERT → 失败块报错 → COMMIT → 异常抛出`，没有 ROLLBACK（E02、E03、E04）。
- 【现状】根因在 pandas：`SQLDatabase` 退出时以无异常参数关闭 ExitStack，SQLAlchemy 事务上下文因此走提交分支
  （REVIEW.md 第 4.1 节 D1，源码 `pandas/io/sql.py:1643-1645` 配合 `sqlalchemy/engine/util.py:143-145`）。
  切换显式 BEGIN 不能消除该行为（E04、E12）。
- 【现状】append 不修改表结构，所以事故后旧列、旧索引仍在，但新数据是“部分写入”；直接按原参数重试会叠加重复行。

### 1.2 传已在外层事务中的 Connection（`engine.begin()` 属主是调用者）

- 【现状】pandas 不创建也不关闭该事务（`run_transaction` 检测到已有事务即裸让出，`pandas/io/sql.py:1649-1653`）。
- 【现状】**异常逃出外层作用域**：外层事务回滚，全部新行撤销，旧行保留（E05、E07）。
- 【现状】**异常在外层作用域内被捕获并正常退出**：外层事务照常提交，已写分块随提交保留（E06、E08、E14、E16）。
  这不是 pandas 替你提交，而是调用者自己的事务边界作出的提交决策。
- 【现状】pandas 对 Connection 入参的书面承诺只有“该事务不会被（pandas）提交”
  （`pandas/core/generic.py:2905-2907`），不包含“失败自动回滚”。

### 1.3 replace 与 append 的失败保证不相同

- 【现状】legacy sqlite3 事务控制下，`DROP TABLE`/`CREATE TABLE` 在物理事务之外自动提交：
  插入尚未开始，旧表（含 CHECK 约束、默认值、索引和旧行）已经不可逆消失（E11、E13 轨迹中 DDL 的
  `active_before=false`）。之后即使外层 ROLLBACK 正确执行，也只能回滚新表上的 DML，旧表不会恢复（E13）。
- 【现状】重建的表只按 DataFrame 列与显式 dtype 生成：新表为 `value BIGINT`，可空、无 CHECK、无默认值、无索引；
  负值在新表上可以写入成功（E17）。不能假定新表继承旧约束。
- 【现状】采用 SQLAlchemy 文档的显式 BEGIN 配置后，DDL 进入物理事务；此时若 Connection 属主且异常逃逸，
  ROLLBACK 可完整恢复旧表、索引与旧行（E15）——这是固定版本下唯一完整原子的替表路径。
  但 Engine 属主时仍被第 1.1 节的提交缺陷击败（E12）。

### 1.4 逻辑事务不等于物理事务

- 【现状】SQLAlchemy 的 begin 事件/`RootTransaction` 在 SQLite 上默认不发任何 SQL
  （`sqlalchemy/engine/default.py:695-696` `do_begin` 为空操作）；legacy 模式下物理 BEGIN 由 sqlite3
  在首条 DML 前隐式发出。begin 事件时刻驱动侧 `in_transaction=False`（E02、E05 轨迹）。
- 【现状】因此不能依据 SQLAlchemy 日志或事件文字断言物理事务状态；只能依据驱动侧 SQL 轨迹与
  sqlite3 `in_transaction` 快照判断。正常事务回归（上游 rollback 断言）验证的是显式事务 API 的回滚能力，
  不能作为 to_sql 分块写入原子性的依据。

## 2. 责任：谁负责失败处理

| 调用方式 | 事务属主 | pandas 的义务 | 调用者必须承担的事项 |
|---|---|---|---|
| Engine 直传 | pandas（固定版本有 D1 缺陷） | 成功时提交；异常原样转发 | 视为无原子性保证：失败后先核对实际行/结构再决定补偿；不得依赖自动回滚 |
| Connection（外层 `engine.begin()`） | 调用者 | 复用事务、不提交不回滚、不吞异常 | 让异常逃逸或显式 rollback 才能获得回滚；捕获后继续即接受提交 |
| replace（legacy 驱动） | 同上，且 DDL 受驱动自动提交支配 | 按序执行 drop/create/insert | 旧表恢复只能靠外部备份；要原子替表必须改用显式 BEGIN 配置 |

- 【责任】任何兼容修复都**不得无条件回滚调用者的事务**：Connection 模式下事务中可能包含调用者的其他合法工作，
  吞异常后提交是调用者的权利（E06、E16）。库层能做的是正确转发异常，而非替属主作决策。
- 【责任】SQLAlchemy 在收到“无异常的上下文退出”时按契约提交，行为本身正确；D1 的修复点在 pandas 的退出路径
  （上游方向，本轮不实现、不改固定版本包）。

## 3. 建议：最小兼容修复方向（未实现，需评审）

1. **append 任务（最小改动，调用者侧）**：改用 `with engine.begin() as conn:` 取得 Connection 后传 `to_sql`；
   不要在该作用域内吞异常，或在捕获分支显式 `conn.rollback()` 后再退出。对照 E05/E07 的回滚正例。
2. **replace 任务**：在上一条基础上，对 Engine 注册 SQLAlchemy 文档的两个事件监听
   （connect 事件 `isolation_level=None`；begin 事件 `exec_driver_sql("BEGIN")`，
   出处 `sqlalchemy/dialects/sqlite/pysqlite.py:362-390`），使 DDL 进入物理事务，对照 E15。
   该配置改变整个 Engine 的事务语义，且不得与 `isolation_level` 执行选项混用；上线前须回归同 Engine 的其他任务。
3. **失败处置流程**：失败后不要只看行数，也不要立即原样重试。按独立连接核对
   `sqlite_master` 对象、`PRAGMA table_info` 列结构和完整行集合（probe 快照口径），
   确认部分写入/替表损失范围后再做补偿；旧表恢复依赖备份，replace 重试本身不会带回旧表。
4. **上游根治方向（非本轮）**：推动 pandas 修正 `SQLDatabase.__exit__` 的异常传递，使 pandas 自有事务在
   异常在途时回滚而非提交；升级版本前以调用者侧方案 1/2 兜底。

## 4. 失败操作（替换原“失败操作”一节）

遇到约束错误或整数溢出：

1. 记录异常类型与消息（`IntegrityError` 来自数据库、cause 为 sqlite3 错误；`OverflowError` 来自 Python 绑定阶段、
   无数据库 cause，二者后果一致，见 E02/E03）。
2. 判断调用方式：Engine 直传默认存在部分写入；Connection 模式按异常是否逃逸判断已回滚或已提交。
3. 用独立连接核对结构与完整行（不是只数行），再决定补偿或重试；replace 失败同时评估旧表备份恢复。
4. 需要原子性保证的任务，按第 3 节改造调用方式后再上线；未改造前不得在任务文档中宣称“失败不改数据库”。

## 5. 证据入口

- `python3 bootstrap.py review_evidence.py run|verify|demo`：18 个单因素案例的固化证据、复核与差异演示。
- `python3 bootstrap.py probe.py normal|incident|replace` 或 `--spec case.json`：单次公开调用观测。
- `python3 bootstrap.py checks.py normal`：保留的上游事务断言与正常分块写入回归（须通过）。
- `python3 bootstrap.py checks.py incident`：按旧承诺作断言，固定版本下稳定失败（`TX_ATOMICITY_MISMATCH`），
  用于持续确认缺陷仍在；它不是正确性证明。
