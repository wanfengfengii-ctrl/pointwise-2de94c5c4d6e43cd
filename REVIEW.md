# REVIEW：pandas 2.2.2 `DataFrame.to_sql` 经 SQLAlchemy 2.0.35 写 SQLite 的分块事务审查

- 审查对象：未修改的 pandas 2.2.2（numpy 1.26.4）、SQLAlchemy 2.0.35、Python 3.11.2、SQLite 3.40.1。
- 源码指纹：`source_manifest.py` 校验的五个模块 SHA-256 全部通过；本文只引用发行包原始模块路径与原始行号（安装路径无关）。
- 证据入口：`probe.py run_case`（公开 `DataFrame.to_sql`，无猴子补丁）；本审查的可重跑固化为 `review_evidence.py`，输出 `evidence.json`（18 个单因素案例）。
- 本轮只审查、只修订说明，不实现生产修复；`checks.py`、`probe.py`、`samples.py`、上游断言与源码指纹均未改动。

---

## 1. 结论摘要（现状与建议严格分开）

### 1.1 现状（由固定版本源码与可重跑证据支持）

1. **传 Engine 时，pandas 自建并拥有事务，但失败清理是错的。** pandas 在 `SQLDatabase.__init__` 中进入 `con.begin()`，异常在途时 `SQLDatabase.__exit__` 仍调用无异常参数的 `ExitStack.close()`，使 SQLAlchemy 事务上下文走**提交分支**。后块失败时，此前已写入的分块被 COMMIT 持久化（缺陷 D1，证据 E02–E04、E12）。
2. **传已在事务中的 Connection 时，pandas 不拥有事务。** `run_transaction()` 检测到 `con.in_transaction()` 为真即原样让出，不 begin、不 commit、不 rollback。异常逃出外层作用域时，外层 `engine.begin()` 回滚全部写入（E05、E07、E15）；异常在作用域内被捕获并正常退出时，外层事务照常提交，已写分块保留（E06、E08、E14、E16）。pandas 文档字符串本身只承诺“该事务不会被提交”，并未承诺失败回滚（`pandas/core/generic.py:2905-2907`）。
3. **legacy sqlite3 驱动下，replace 的 DDL 在物理事务之外自动提交。** `DROP TABLE`/`CREATE TABLE` 执行时 `sqlite3.in_transaction=False`，旧表、CHECK 约束、默认值、索引和旧行在插入阶段开始前已不可逆丢失；后续回滚只能作用于新表的 DML（缺陷 D2，证据 E11、E13、E14）。
4. **显式 BEGIN 配方（SQLAlchemy 文档同款，`pysqlite.py:362-390`）把 DDL 纳入物理事务**，使“外层事务 + 异常逃逸”成为矩阵中唯一完整原子的 replace 路径（E15）；但它**不能修复 D1**——Engine 模式下 pandas 仍会提交失败写入（E04、E12）。
5. **逻辑事务与物理事务是两个对象。** SQLAlchemy 的 begin 事件/`RootTransaction` 在 legacy 模式下不发出任何 SQL（`DefaultDialect.do_begin` 为空操作）；物理 BEGIN 由 sqlite3 在首条 DML 前隐式发出。原说明第 4 条“begin 上下文建立后物理事务已经开始”不成立（证据 E02/E05 轨迹中 begin 事件的 `sqlite_active=false`）。

### 1.2 建议（本轮不实现，见第 7 节）

- 发布阻断：现行“失败不改数据库”的操作承诺与实现不符，**以 Engine 为入参的分块写入必须视为无原子性保证**，阻断依赖该承诺的任务上线。
- 最小兼容修复方向：调用者侧用自有 `engine.begin()` 外层事务 + 不吞异常（或显式 rollback）取得 append 原子性；replace 原子性还须叠加显式 BEGIN 配置。不得在库内无条件替调用者回滚——Connection 模式下事务归属调用者，回滚会销毁调用者的其他工作。

---

## 2. 调用链（带原始源码行号）

### 2.1 公开入口到 pandas 事务对象

1. `pandas/core/generic.py:2873-3098` `NDFrame.to_sql`（`@final` 包装），文档字符串 `generic.py:2905-2907` 明确：传入已在事务中的 SQLAlchemy Connection 时“the transaction will not be committed”；传入 sqlite3 原生连接时“it will not be possible to roll back the record insertion”。
2. `pandas/io/sql.py:746-854` 模块级 `to_sql`：第 841 行 `with pandasSQL_builder(con, schema=schema, need_transaction=True) as pandas_sql:`，第 842-854 行委托 `pandas_sql.to_sql(...)`。
3. `pandas/io/sql.py:885-919` `pandasSQL_builder`：SQLAlchemy Engine/Connection 在第 906 行构造 `SQLDatabase(con, schema, need_transaction)`。
4. `pandas/io/sql.py:1620-1641` `SQLDatabase.__init__`（所有权判定的核心）：
   - 第 1631 行 `self.exit_stack = ExitStack()`；
   - 第 1635-1636 行：**Engine 入参** → `con = self.exit_stack.enter_context(con.connect())`，pandas 自建连接并登记清理；
   - 第 1637-1638 行：`if need_transaction and not con.in_transaction(): self.exit_stack.enter_context(con.begin())` —— 仅当连接上没有活动事务时才进入新事务，并把该事务上下文交给同一个 ExitStack；
   - 第 1639 行 `self.con = con`。
5. `pandas/io/sql.py:1643-1645` `SQLDatabase.__exit__`：`if not self.returns_generator: self.exit_stack.close()` —— **不接收也不转发异常信息**。这是 D1 的 pandas 侧根因（第 4.1 节展开）。

### 2.2 DDL 路径（replace）

6. `pandas/io/sql.py:1944-2031` `SQLDatabase.to_sql`：第 2008-2016 行 `prep_table(...)`，第 2018-2028 行 `sql_engine.insert_records(...)`，第 2030 行 `check_case_sensitive`。
7. `pandas/io/sql.py:1867-1913` `prep_table`：第 1902-1911 行构造 `SQLTable`，第 1912 行 `table.create()`。
8. `pandas/io/sql.py:983-995` `SQLTable.create`：表已存在且 `if_exists=="replace"` 时，第 988 行 `drop_table`、第 989 行 `_execute_create()`。
9. `pandas/io/sql.py:2056-2064` `drop_table`：第 2062-2063 行 `with self.run_transaction(): self.get_table(...).drop(bind=self.con)`。
10. `pandas/io/sql.py:977-981` `_execute_create`：第 980-981 行 `with self.pd_sql.run_transaction(): self.table.create(bind=...)`。
11. `pandas/io/sql.py:1647-1653` `run_transaction`：`if not self.con.in_transaction(): with self.con.begin(): yield ...` 否则**裸 `yield self.con`**——嵌套于外层事务时 pandas 不产生任何事务边界。

### 2.3 分块 DML 路径

12. `pandas/io/sql.py:1543-1567` `SQLAlchemyEngine.insert_records`：第 1557-1558 行 `try: return table.insert(chunksize=chunksize, method=method)`；仅对特定 MySQL `StatementError` 做文案转换后原样 `raise err`（第 1559-1567 行），**无任何回滚/提交处理**。
13. `pandas/io/sql.py:1082-1126` `SQLTable.insert`：第 1109 行计算块数；第 1111 行 `with self.pd_sql.run_transaction() as conn:`；第 1112-1119 行逐块 `exec_insert(conn, keys, chunk_iter)`，异常直接逃出该 `with`。
14. `pandas/io/sql.py:997-1011` `_execute_insert`（`method=None`）：第 1010 行 `conn.execute(self.table.insert(), data)`，以 `executemany` 一次发送一个分块。

### 2.4 SQLAlchemy 事务与驱动边界

15. `sqlalchemy/engine/base.py:795-869` `Connection.begin`：第 860-862 行创建 `RootTransaction`；文档字符串第 841-844 行明确“From a database point of view ... does not emit any SQL or change the state of the underlying DBAPI connection”。
16. `sqlalchemy/engine/base.py:2652-2757` `RootTransaction`：第 2683 行 `_connection_begin_impl()`；第 2709-2723 行 `_close_impl` 在仍活动时回滚；第 2731-2749 行 `_do_commit` 调 `_commit_impl` 成功后摘除事务。
17. `sqlalchemy/engine/base.py:1091-1111` `_begin_impl`：第 1103-1104 行派发 `begin` 事件，第 1107 行 `dialect.do_begin(...)`。
18. `sqlalchemy/engine/default.py:695-696` `DefaultDialect.do_begin`：`pass`（空操作）；同文件第 698-702 行 `do_rollback`→`dbapi_connection.rollback()`、`do_commit`→`dbapi_connection.commit()`。
19. `sqlalchemy/engine/base.py:1131-1146` `_commit_impl`：第 1132-1133 行先派发 `commit` 事件，第 1144 行 `dialect.do_commit`。
20. `sqlalchemy/engine/base.py:1113-1129` `_rollback_impl`：先 `rollback` 事件（1114-1115），再 `dialect.do_rollback`（1127）。
21. `sqlalchemy/engine/util.py:129-167` `TransactionalContext.__exit__`：**第 143-145 行，`type_ is None and self._transaction_is_active()` 时 `self.commit()`；否则第 155-162 行 `self.rollback()`**。上下文是否拿到异常，决定提交还是回滚。
22. `sqlalchemy/engine/base.py:2225-2360` `_handle_dbapi_exception`：第 2348-2349 行仅在 `not self.in_transaction()` 时才 `_rollback_impl()`（autorollback）；存在活动 RootTransaction 时语句错误**不触发驱动回滚**，只包装异常并抛出（第 2353-2355 行）。
23. `sqlalchemy/engine/default.py:1803-1804` `ExecutionContext.handle_dbapi_exception`：`pass`，SQLite 无方言级异常清理。
24. `sqlalchemy/engine/base.py:1038-1040` `in_transaction`：返回 SQLAlchemy 逻辑事务状态（`_transaction is not None and is_active`），与 sqlite3 物理事务是两个独立状态。
25. `sqlalchemy/engine/base.py:3216-3244` `Engine.begin`：`with self.connect() as conn: with conn.begin(): yield conn`；文档承诺成功提交、出错回滚（第 3228-3230 行）。
26. `sqlalchemy/dialects/sqlite/pysqlite.py:340-410`：驱动文档“Serializable isolation / Savepoints / Transactional DDL”，第 357-360 行说明 SQLAlchemy 默认不改变 pysqlite 的遗留行为；第 362-390 行给出显式 BEGIN 配方（connect 事件置 `isolation_level=None`，begin 事件 `exec_driver_sql("BEGIN")`），第 381-385 行附混用 isolation_level 的警告。
27. `sqlalchemy/dialects/sqlite/pysqlite.py:537-542` `set_isolation_level`：非 AUTOCOMMIT 时把 dbapi `isolation_level` 置为 `""`（legacy 隐式事务控制）。

---

## 3. 三种事务义务与驱动边界的划分

| 主体 | 义务（源码可证） | 不承担的事项 |
|---|---|---|
| pandas（Engine 入参） | 自建连接（`sql.py:1635-1636`）、在无活动事务时进入 `con.begin()`（1637-1638）、退出时关闭 ExitStack（1643-1645） | 源码中**没有**“异常时回滚”的分支；`insert_records` 仅转抛异常（1559-1567） |
| pandas（Connection 入参） | 复用调用者连接；`run_transaction` 裸 yield（1652-1653），不创建边界 | 不 commit、不 rollback、不拦截异常；文档只保证不替你提交（`generic.py:2905-2907`） |
| 调用者外层 `engine.begin()` | 作用域正常退出→提交；异常逃逸→回滚（`util.py:143-162`，`base.py:3228-3230`） | 无法替 pandas 修复其自有 ExitStack 的提交（Engine 模式下外层事务根本不存在） |
| SQLAlchemy 逻辑层 | 维护 RootTransaction 状态机、事件、autorollback 条件（`base.py:2348`） | `do_begin` 对 SQLite 为空操作（`default.py:695-696`），不保证物理事务已开启 |
| sqlite3 驱动（legacy） | DML 前隐式发 BEGIN；`commit()/rollback()` 透传 SQL | DDL 前不发 BEGIN 且自动提交既有事务；事务内 DDL 不具备可回滚性（`pysqlite.py:340-360`） |

**逻辑事务 vs 物理事务**：probe 轨迹同时记录两个状态。`sqlalchemy` 事件里的 `sa_active` 取自 `Connection.in_transaction()`（逻辑），`sqlite_active` 取自 `driver_connection.in_transaction`（物理）。legacy 模式下 begin 事件时刻为 `(sa_active=false, sqlite_active=false)`——逻辑 RootTransaction 刚建立，物理 BEGIN 尚未发出；显式 BEGIN 模式下 begin 事件回调先执行 `BEGIN`，故事件时刻已是 `sqlite_active=true`（E07/E10/E15 轨迹）。

---

## 4. 两个已确认缺陷的机制

### 4.1 D1：pandas 在异常在途时提交自有事务（Engine 模式）

证据调用栈（独立实验，未改生产代码；与 E02 同构）显示 COMMIT 的精确来源：

```
pandas/io/sql.py:841            with pandasSQL_builder(..., need_transaction=True)
pandas/io/sql.py:1645           SQLDatabase.__exit__ -> self.exit_stack.close()
contextlib.py ExitStack.close   以 __exit__(None, None, None) 进入各上下文
sqlalchemy/engine/util.py:145   if type_ is None ...: self.commit()
sqlalchemy/engine/base.py:2631  Transaction.commit -> _do_commit (2736)
sqlalchemy/engine/base.py:1144  _commit_impl -> dialect.do_commit
sqlalchemy/engine/default.py:702  dbapi_connection.commit()   # 物理 COMMIT
```

时序（E02，values=[1,2,-3,4]，chunksize=2）：

1. pandas `SQLDatabase.__init__` 进入 `con.begin()`，SQLAlchemy 仅建逻辑 RootTransaction（`do_begin` 空操作）。
2. 首块 INSERT 触发 sqlite3 隐式 `BEGIN`（轨迹 `active_before=false → true`），写入 1、2。
3. 第二块首个 INSERT(-3) 触发 CHECK 失败，`IntegrityError` 经 `_handle_dbapi_exception` 包装抛出；因 `in_transaction()` 为真，autorollback 被跳过（`base.py:2348`）。
4. 异常沿 `SQLTable.insert`（1111 的 with）、`insert_records`、模块级 `to_sql`（841 的 with）向上传播。
5. `SQLDatabase.__exit__` 调 `exit_stack.close()`，ExitStack 对 `con.begin()` 登记的 `RootTransaction` 以**无异常参数**调用 `__exit__`，`util.py:143` 条件成立 → `commit()` → 物理 COMMIT。首块 1、2 被持久化，异常随后继续抛给调用者（`to_sql_raise` 在 COMMIT 之后入轨迹）。

E03 同构，区别仅在于第二块在 Python 绑定阶段抛 `OverflowError`（SQL 尚未发出，故只有 2 条 INSERT 轨迹），提交结果相同。E04 证明显式 BEGIN 不改变该结果：物理事务虽真实存在，但提交决策发生在 pandas 的 ExitStack，驱动配方无权干预。

**归属判定**：这是 pandas 2.2.2 的实现缺陷（`SQLDatabase.__exit__` 未把异常状态传入 ExitStack，等价于“失败也提交”），不是 SQLAlchemy 事务状态机的 bug——SQLAlchemy 在收到无异常 `__exit__` 时按契约提交（`util.py:143-145`）。

### 4.2 D2：legacy 驱动下 replace 的 DDL 自动提交，旧表不可恢复

E13 轨迹（connection 属主、异常逃逸、外层确有 ROLLBACK）：

```
active_before=false | DROP TABLE readings          # 无物理事务，sqlite3 自动提交
active_before=false | CREATE TABLE readings (...)  # 同上，永久生效
active_before=false | BEGIN                        # 插入阶段才开启物理事务
active_before=true  | INSERT (1) / INSERT (2)
sa rollback 事件 → ROLLBACK                         # 只回滚 DML
```

最终结构：新表 `readings(value BIGINT)`（可空、无 CHECK、无默认值）、索引 `readings_value` 消失、旧行 90 消失、新表 0 行。外层 ROLLBACK 真实发生且符合契约，但它能触及的范围不含已自动提交的 DDL。E11（Engine 属主）在 D2 之上叠加 D1，新表还留下首块两行。E17 从另一侧面证实约束边界：replace 后新表无 CHECK，-3 被正常接受并提交，说明“替表继承旧约束”的假设不成立。

### 4.3 显式 BEGIN 的隔离效果（单因素对照）

E15 相对 E13 仅改 `begin: legacy→explicit`：connect 事件禁用 sqlite3 隐式控制（`isolation_level=None`），begin 事件主动发 `BEGIN`。轨迹中 DROP/CREATE 的 `active_before=true`，DDL 进入物理事务；异常逃逸后外层 ROLLBACK 把 DDL 与 DML 一起撤销，旧结构、索引、旧行完整恢复。这证明 D2 的根因是驱动事务控制方式而非 SQLite 本身不支持事务性 DDL。但 E12（同配置、Engine 属主）仍被 D1 击败：DDL 已在物理事务内，pandas 却选择提交。

---

## 5. 逐案例证据矩阵（chunksize=2；期望独立于观测给出）

旧结构 = 表 `readings(value INTEGER NOT NULL CHECK(value>=0), legacy TEXT NOT NULL DEFAULT 'old')` + 索引 `readings_value`；旧行 = `(90,'old')`。“驱动调用”计数来自 sqlite trace 回调。完整输入、原始 trace、独立期望与报告定位见 `evidence.json`。

<a id="E01"></a>**E01 append / engine / legacy / 成功（正例对照）** — values [1,2,3,4]
- 独立期望：无错误、返回 4、旧结构保留、五行齐全、BEGIN×1 COMMIT×1 ROLLBACK×0。实际：一致（MATCH）。
- 意义：证明成功路径与 `checks.py normal` 断言；pandas 自有事务在成功时行为正确。

<a id="E02"></a>**E02 append / engine / legacy / CHECK 失败（公开事故，缺陷 D1）** — [1,2,-3,4]
- 原子性承诺（独立应然）：新行 0 存活、仅旧行 90、驱动应 ROLLBACK×1。
- 实际：`IntegrityError` 逃出外层；行 = [1,2,90]；BEGIN×1 **COMMIT×1** ROLLBACK×0；结构未变（append 不动 DDL）。
- 核对：旧结构在；旧行 90 在；已写分块 [1,2] 被错误提交。`checks.py incident` 因此稳定报 `TX_ATOMICITY_MISMATCH`（本轮保留不改）。

<a id="E03"></a>**E03 append / engine / legacy / 整数溢出（D1，换异常类型单因素）** — [1,2,2⁶³,4]
- 实际：`OverflowError`（`cause_type=null`，Python 绑定阶段抛出，非数据库错误）；行 = [1,2,90]；同样 COMMIT×1。
- 结论：D1 与异常类型无关，约束错误和绑定期溢出后果相同。

<a id="E04"></a>**E04 append / engine / explicit / CHECK 失败（D1，begin 方式单因素）**
- 实际：begin 事件时 `sqlite_active=true`（物理事务先建），但仍 COMMIT×1、行 [1,2,90]。
- 结论：显式 BEGIN 单独使用不能救 Engine 模式；提交决策在 pandas ExitStack。

<a id="E05"></a>**E05 append / connection / legacy / 异常逃逸（完整回滚正例）**
- 独立期望：`IntegrityError` 出现在 `outside_transaction_scope`；仅旧行 90；ROLLBACK×1。实际一致。
- 机制：pandas `run_transaction` 裸 yield；异常进入 `engine.begin()` 的 `__exit__`，`type_ is not None` → 回滚（`util.py:155-162`）。

<a id="E06"></a>**E06 append / connection / legacy / 内部捕获后提交（调用者责任）**
- 实际：错误记录于 `inside_outer_transaction`，`outer_scope_continues` 后作用域正常退出 → COMMIT×1；行 [1,2,90]。
- 结论：这不是 pandas 提交的——外层事务归属调用者，捕获并继续即意味着调用者选择提交。任何“库内自动回滚”方案都会在此场景错误地销毁调用者事务中的其他工作。

<a id="E07"></a>**E07 append / connection / explicit / 异常逃逸（回滚正例）**
- 实际：ROLLBACK×1、仅 90；与 E05 单因素（begin）对照，两种 begin 方式下“逃逸即回滚”都成立。

<a id="E08"></a>**E08 append / connection / explicit / 内部捕获后提交（调用者责任）**
- 实际：COMMIT×1、行 [1,2,90]；E06 的显式 BEGIN 对照，结论相同。

<a id="E09"></a>**E09 append / connection / legacy / 成功（正例）**
- 实际：返回 4、五行齐全、COMMIT×1；外层事务一次性提交全部两个分块。

<a id="E10"></a>**E10 append / connection / explicit / 成功（正例）**
- 实际：与 E09 同构；begin 事件时刻物理事务已活动。

<a id="E11"></a>**E11 replace / engine / legacy / 溢出（D2+D1 叠加）** — [1,2,2⁶³,4]
- 原子性承诺：旧结构、索引、旧行全部恢复，ROLLBACK×1。
- 实际：DROP/CREATE 在 `active_before=false` 下自动提交；随后 BEGIN、INSERT 1/2；pandas ExitStack COMMIT。终态：仅新表 `value BIGINT`（可空）、行 [1],[2]、无索引、无 90、无 legacy 列。
- 四项核对：旧列丢失；旧索引丢失；旧行丢失；已写分块 [1,2] 保留。

<a id="E12"></a>**E12 replace / engine / explicit / 溢出（D1，DDL 已受物理事务保护）**
- 实际：DROP/CREATE 的 `active_before=true`（本可回滚），但 pandas COMMIT 整体；终态与 E11 相同。
- 意义：隔离出 D1 的独立贡献——即使驱动边界正确，pandas 的退出路径仍破坏原子性。

<a id="E13"></a>**E13 replace / connection / legacy / 异常逃逸（D2 纯效应）**
- 实际：外层 ROLLBACK×1 真实发生；终态为**空的新表**（BIGINT、可空），旧结构/索引/90 不恢复。
- 意义：这是“正确执行的回滚也救不回自动提交 DDL”的干净样本，责任在驱动事务控制方式（D2），不是外层事务失约。

<a id="E14"></a>**E14 replace / connection / legacy / 内部捕获（D2 + 调用者提交）**
- 实际：捕获后 COMMIT；新表 + 行 [1],[2]。即使没有 D1（Connection 模式 pandas 不掌控边界），legacy DDL 损失依旧，且调用者的提交把首块也固化。

<a id="E15"></a>**E15 replace / connection / explicit / 异常逃逸（唯一完整原子 replace 正例）**
- 独立期望：旧对象（含索引）、旧两列、旧行 90 全部恢复、ROLLBACK×1。实际一致（MATCH）。
- 发布含义：原子替表在固定版本下是可达成的，但需同时满足三个条件——显式 BEGIN 配置、Connection 属主的外层事务、异常必须逃逸（或显式 rollback）。

<a id="E16"></a>**E16 replace / connection / explicit / 内部捕获（调用者责任）**
- 实际：与 E15 仅 `catch` 一单因素之差；捕获后 COMMIT，新表与 [1],[2] 全部保留。
- 意义：同样的正确配置下，吞异常即放弃原子性；责任与处置权在调用者。

<a id="E17"></a>**E17 replace / connection / legacy / CHECK 输入但成功（约束边界正例）** — [1,2,-3,4]
- 实际：无错误、返回 4、行含 -3；新表无 CHECK 约束。
- 意义：证明 replace 重建的表不继承旧约束；“替表后约束仍在”的任何运维推断都不成立。

<a id="E18"></a>**E18 replace / connection / legacy / 成功（替表正例）**
- 实际：返回 4、新表四行、无索引、COMMIT×1；作为 replace 成功路径的结构基线（与 E17 共享新表结构，差别仅在输入是否触发约束）。

### 5.1 矩阵覆盖说明（单因素隔离）

- owner 隔离：E02 vs E05（engine→connection，其余相同）；begin 隔离：E02 vs E04、E05 vs E07、E13 vs E15；catch 隔离：E05 vs E06、E07 vs E08、E15 vs E16；if_exists 隔离：E03→E11 同值跨 append/replace；异常类型隔离：E02 vs E03。
- 正例保留：成功提交 E01/E09/E10/E18/E17；真正完整回滚 E05/E07/E15；上游 `test_transaction_rollback` 特化版由 `checks.py normal` 与 `review_evidence.py verify` 持续执行。
- 每个替表案例均核对 sqlite_master 对象、PRAGMA table_info 列结构与完整行，而非仅比较行数或异常类型。

---

## 6. 异常传播与事务归属的规则（可直接用于代码评审）

1. **异常转发链不变**：sqlite3/绑定期异常 → SQLAlchemy 包装（`_handle_dbapi_exception`，`base.py:2225`）→ pandas 不吞不转义（`insert_records` 第 1559-1567 行仅 MySQL 特判后重抛）→ 调用者收到的仍是原始语义的 `IntegrityError`/`OverflowError`（probe 的 `cause_type` 可证：前者 cause 为 sqlite3 IntegrityError，后者无 cause）。
2. **Engine 属主**：事务是 pandas 的。调用者既不能回滚（拿不到 Connection），也不能阻止提交；失败后的部分写入是当前版本的既定行为。
3. **Connection 属主**：事务是调用者的。pandas 只保证不替你提交（`generic.py:2905-2907`）。要原子性，异常必须逃出 `engine.begin()` 或由调用者显式 `conn.rollback()`；捕获后继续等于接受全部待提交内容。
4. **DDL/DML 边界**：legacy sqlite3 下 DDL 自动提交、DML 隐式开事务；显式 BEGIN 下两者同处一个物理事务。SQLAlchemy 逻辑事务事件不能用来推断物理事务状态，必须以驱动侧 `in_transaction`/SQL 轨迹为准（CONTRACT 第 52 行同款要求）。
5. **autorollback 不适用于本事故**：`base.py:2348` 的条件在 RootTransaction 活动时为假，语句错误不会自动 ROLLBACK；不要把 1.x 时代的 autorollback 经验当作保护。

---

## 7. 发布阻断与最小兼容修复取舍（建议，本轮不实现）

### 7.1 发布阻断项

1. **阻断**任何以 Engine 直传 `to_sql` 且依赖“失败回滚/失败不改库”承诺的任务：D1 在两种 begin 方式、两类异常下均稳定复现（E02-E04、E12）。
2. **阻断**legacy 配置下把 `if_exists="replace"` 用于不可重建的表：D2 使旧表在 DDL 完成点即丢失，与后续成败无关（E11、E13、E14、E18 的无索引新表基线）。
3. 运维侧“只看行数判断任务状态”的规程阻断：replace 失败可能留下 0 行的**错误结构**新表（E13），行数与初始状态同样为 1 行 vs 0 行的对比无法识别结构丢失；必须核对 sqlite_master/table_info。

### 7.2 最小兼容修复选项（按侵入度排序，均不在本轮落地）

| 选项 | 做法 | 覆盖的缺陷 | 兼容性代价 |
|---|---|---|---|
| A（调用者侧，最小） | 用 `with engine.begin() as conn:` 包裹，传 Connection；异常不捕获或捕获后显式 rollback | append 下 D1（E05/E07 模式） | 改调用点；调用者需自行处理重试与幂等；不解决 legacy D2 |
| B（A + 驱动配置） | 叠加 pysqlite 文档显式 BEGIN 两个事件监听（`pysqlite.py:370-379`） | D1 规避 + D2 解除，replace 获完整原子性（E15 模式） | 引擎级配置变更；该 Engine 上所有连接改变事务语义；文档警告勿混用 isolation_level（381-385 行）；需回归既有任务 |
| C（库内修复，上游方向） | 修正 `SQLDatabase.__exit__`，把异常状态传入 ExitStack（异常在途时对自有 `con.begin()` 走 rollback） | D1 根治 | 改 pandas 行为，需上游 issue/PR 与版本升级；本工作区固定 2.2.2，不能本地改包（SOURCE.md 约束） |
| D（通用原子替表框架） | 临时表 +  rename 等 | 替表场景通用化 | 超出本轮范围；CONTRACT 明确不开发 |

**取舍红线**：不得在 pandas/SQLAlchemy 包装层无条件回滚。Connection 模式下事务所有权属于调用者（E06/E08/E14/E16 表明调用者可能有意在同一事务里做更多工作后再提交），无条件 rollback 会破坏合法用法；正确做法是让异常按所有权边界传播，由属主决定提交或回滚。也不能把“加一个事务上下文”当作未经核实的原子性承诺——E12 证明仅有物理 BEGIN 而不修正属主退出路径，原子性仍不成立。

### 7.3 重试语义提示

- D1 场景下“按同样参数重试”会在已提交首块的基础上再次插入，若表无唯一约束将产生重复行；重试前必须先核对实际行（本审查证据显示失败后行集合已改变）。
- replace 重试不会恢复旧表，只会再一次 DROP/CREATE；旧表恢复只能依赖外部备份，本题范围不含进程重开或持久文件恢复。

---

## 8. 可重跑证据与复核记录

- 生成：`python3 bootstrap.py review_evidence.py run` → `evidence.json`（每案含完整 spec、独立期望 `expectation`/`atomic_promise`、原始观测 `actual`、完整 `raw_trace`、`review_reference`）。
- 复核：`python3 bootstrap.py review_evidence.py verify` —— 先跑源码指纹与上游 rollback 断言，再逐案重跑比对保存观测、校验 DDL 物理边界注解与 REVIEW 锚点；对 D1/D2 断言差异仍能复现，而非要求缺陷消失。
- 演示：`python3 bootstrap.py review_evidence.py demo` —— 成功、Engine 部分提交、legacy DDL 丢失、外层回滚、显式 BEGIN 原子替表、捕获后提交六类真实结构/数据差异。
- 保留命令：`checks.py normal` 仍通过（`NORMAL_REGRESSION_OK`）；`checks.py incident` 仍以 `TX_ATOMICITY_MISMATCH` 失败，符合 CONTRACT 对事故命令的定位。
- 观测方法学：每案独立命名内存库 + 锚连接保活 + NullPool，写连接 dispose 后以独立 sqlite3 连接读结构与行；UUID 不入输出；结论不依赖 SQLAlchemy 日志文字，只依赖驱动 SQL 轨迹与双端事务状态。
