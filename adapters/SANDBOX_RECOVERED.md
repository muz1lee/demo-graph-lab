# `adapters/sandbox.py` 恢复记录（源码已丢失）

**状态**：源文件 `adapters/sandbox.py` **不存在于磁盘，也从未进入 git**。本文件是 2026-07-30
从遗留字节码 `adapters/__pycache__/sandbox.cpython-313.pyc`（19664 B，mtime 2026-07-26 12:21）
用 `python3.13` 反射提取出的完整接口契约，用于将来按图重建。

- 字节码原件已备份至 `~/Backups/demo-graph-lab-20260730/orphan_pyc/`（连同其测试
  `adapters/tests/test_broker_and_sandbox.cpython-313-pytest-8.4.2.pyc`，11385 B）。
- 5090 与本机全盘搜索均无 `sandbox.py` 源码副本（2026-07-30 核实）。
- 字节码为 CPython 3.13 格式；本机默认解释器是 3.12.0，需显式用 `python3.13` 读取。

**为什么值得留**：当前 Phase 1 执行生成代码用的是 `harness/phase1.py:26-29` 的
`exec(code, {"__builtins__": {}})`，防护面远小于本模块。Phase 2 跑成批对照组时
需要这一层，届时按下方契约重建即可。

---

## 1. 模块 docstring（原文逐字）

```
Generated-policy validator and isolated subprocess RPC runner.

This layer supplies defense in depth:

* a strict AST allowlist rejects imports, file/process/network primitives and
  reflective Python features;
* the child receives a minimal builtin set and a Python audit hook that denies
  filesystem, socket and process events;
* the child environment is rebuilt from a small non-sensitive allowlist;
* all external effects go through MethodBroker JSON-lines RPC.

This is not a replacement for deployment isolation.  Production must also run
the child in a container/namespace with no network and
```

（末句在常量池中被截断，原文应续「…no writable filesystem」一类表述。）

## 2. 公开符号

| 符号 | 类型 | 说明 |
|---|---|---|
| `PolicySecurityError` | Exception | AST 校验失败 / 触碰禁用面 |
| `PolicyExecutionError` | Exception | 子进程异常退出、协议违规 |
| `PolicyTimeoutError` | Exception | 超时 |
| `PolicyRunResult` | dataclass(frozen, slots) | 字段 `(result, rpc_calls, returncode, stderr)` |
| `IsolationSelfTest` | dataclass(frozen, slots) | 字段 `(network_blocked, sensitive_environment_absent)` |
| `PolicyAstValidator` | class | 见 §3 |
| `GeneratedPolicyRunner` | class | 见 §4 |
| `_decode_protocol_line(line: str) -> dict[str, Any]` | 私有函数 | 解析子进程 JSON-lines |
| `_read_stderr(process: subprocess.Popen[str]) -> str` | 私有函数 | 收集 stderr |

## 3. `PolicyAstValidator`

> docstring：`Fail-closed validator for the intentionally small generated DSL.`

```python
def __init__(self, max_nodes: int = 2000) -> None
def validate(self, source: str) -> ast.Module
def generic_visit(self, node: ast.AST) -> None
def visit_Name(self, node: ast.Name) -> None
def visit_Attribute(self, node: ast.Attribute) -> None
```

内部状态：`_max_nodes`、`_node_count`。

**禁用名单（frozenset 原文）**：

```python
{'eval', 'exec', 'compile', 'open', 'input', 'help', 'dir', 'vars',
 'locals', 'globals', 'getattr', 'setattr', 'delattr',
 'memoryview', 'breakpoint', '__import__'}
```

**校验规则与错误消息（行为契约，逐字）**：

| 触发条件 | 消息 |
|---|---|
| 空源码 | `policy source must be non-empty` |
| 语法错误 | `invalid policy syntax at line {n}: {detail}` |
| 出现非顶层函数定义的语句 | `policy may contain only top-level function definitions` |
| `policy_main` 不唯一 | `policy must define exactly one policy_main(api, policy_input)` |
| `policy_main` 参数不是 2 个位置参 | `policy_main must have exactly two positional parameters` |
| 节点数超预算 | `policy exceeds AST node budget {max_nodes}` |
| 出现禁用语法节点 | `forbidden syntax {NodeType} at line {n}` |
| 名字命中禁用名单，或以 `_` 开头 | `forbidden name {name}` |
| 属性名以 `_` 开头（反射面） | `private/reflection attribute is forbidden at line {n}` |

`compile(..., mode='exec')`；节点计数在 `generic_visit` 内累加。

## 4. `GeneratedPolicyRunner`

```python
def __init__(self, broker: MethodBroker, timeout_s: float = 10.0,
             max_rpc_calls: int = 100,
             validator: PolicyAstValidator | None = None) -> None
def run(self, source: str, policy_input: Any) -> PolicyRunResult
@classmethod
def isolation_self_test(cls) -> IsolationSelfTest
@staticmethod
def _spawn(encoded_source: str, encoded_input: str, mode: str) -> subprocess.Popen[str]
```

内部状态：`_broker`、`_timeout_s`、`_max_rpc_calls`、`_validator`。
构造校验：`timeout_s must be positive`、`max_rpc_calls must be positive`。

**子进程隔离（`_spawn`）**：

- 解释器参数 `-I`（isolated：忽略环境与 user site）、`-S`（不加载 site）、`-c`
- **环境变量白名单只有三个**：`PYTHONHASHSEED`、`PYTHONIOENCODING`、`DEMO_GRAPH_POLICY_SANDBOX`
  （后者取值 `'0'` / `'1'`，用于让子进程知道自己处在沙箱模式）
- `Popen(..., stdin=, stdout=, stderr=, text=True, encoding=, env=, close_fds=True)`

**RPC 协议**：JSON-lines，`json.dumps(..., separators=(',', ':'))`，源码与输入以
`utf-8` / `ascii` 编码后传入。消息 `type` 只允许两种：

| type | 含义 |
|---|---|
| `call` | 子进程请求宿主执行一次 broker 调用，计入 RPC 预算 |
| `final` | 子进程给出最终 `result` |

**运行期错误消息（逐字）**：

- `generated policy exceeded {timeout_s:.3f}s timeout`
- `generated policy exceeded RPC budget {max_rpc_calls}`
- `generated policy emitted unsupported message type {type}`
- `generated policy did not exit after final result`（发出 final 后等待 `2.0` s）
- `generated policy exited {returncode}: {stderr}`
- `generated policy exited without a final result`
- `generated policy protocol message must be an object`

stderr 截断长度 `500`。

**`isolation_self_test`**：以 `mode='self-test'` 启动子进程、超时 `5.0` s，让子进程自报
`visible_environment` 并回报两项：

- `network_blocked` —— audit hook 是否成功拒绝 socket
- `sensitive_environment_absent` —— 可见环境变量中不含任何包含
  `KEY` / `TOKEN` / `SECRET` / `PASSWORD` / `CREDENTIAL` 的名字

失败消息：`sandbox isolation self-test timed out`、`sandbox isolation self-test failed: {…}`、
`sandbox self-test returned invalid environment report`。

## 5. 重建指引

1. 依赖 `MethodBroker`（`adapters/method_broker.py`，**源码仍在**）——本模块所有外部作用经它转发。
2. 先写 `PolicyAstValidator`：`ast.NodeVisitor` 子类，`visit_Name` / `visit_Attribute` 覆写，
   `generic_visit` 里做节点计数与语法白名单。fail-closed（默认拒绝）。
3. 再写 `GeneratedPolicyRunner._spawn`：注意 `-I -S` 与三变量环境白名单是隔离的关键。
4. 子进程侧的 audit hook（`sys.addaudithook`）拒绝 `open` / `socket` / `subprocess` 类事件——
   这部分代码在字节码里是子进程 `-c` 的字符串常量，未能完整提取，需重写。
5. 用 §3/§4 的错误消息表做验收：消息逐字一致即可认为行为等价。

## 6. 已知未能提取的部分

- 子进程 `-c` 执行的引导代码全文（含 audit hook 实现与最小 builtin 集的确切内容）。
- 各方法的具体控制流（只有常量、签名与消息可靠还原；分支顺序需重新设计）。
- 原测试 `test_broker_and_sandbox.py` 的断言细节（字节码已备份，未提取）。
