# DataFrame 安全 — 代码审查检查清单

**用途**: 在 Pull Request 审查时使用，防止 DataFrame 布尔操作错误

---

## 快速检查

审查 Python 代码时，特别注意这些模式：

### 🔴 CRITICAL - 如果发现这些，必须修复

- [ ] `if df:` — 直接 DataFrame 布尔转换
- [ ] `if not df:` — 加 not 的布尔转换  
- [ ] `.get('key') or fallback` — OR 操作，其中第一个值可能是 DataFrame
- [ ] `return df or {}` — 返回语句中的 OR
- [ ] `df_a if df_b else df_c` — 条件表达式比较两个 DataFrame

### 🟠 HIGH - 发现时应该询问作者

- [ ] `and df.empty` — 没有先进行 None 检查
- [ ] `or df.something` — OR 操作和 DataFrame 属性
- [ ] 函数返回 DataFrame | dict | list — 需要确认类型转换安全

### 🟡 MEDIUM - 留意但不一定要改

- [ ] DataFrame 通过网络或 API 传输 — 需要序列化
- [ ] 多个 DataFrame 的比较操作 — 需要确认语义

---

## 审查步骤

### 1. 搜索危险模式

```bash
# 在 PR 中搜索
git diff HEAD~1 | grep -E 'if .*_df|\.get.*or |return.*or '
```

### 2. 检查返回值类型

对于返回可能是 DataFrame 或 None 的函数：

```python
# ❌ 不要这样写
def get_results():
    return df or {}  # 危险！

# ✅ 要这样写
def get_results():
    if df is not None and not df.empty:
        return df
    return {}
```

### 3. 检查 API 响应处理

对于从 API 返回的 DataFrame：

```python
# ❌ 不安全
data = api_response.get("results") or {}

# ✅ 安全
data = api_response.get("results")
if data is None or (isinstance(data, pd.DataFrame) and data.empty):
    data = {}
```

### 4. 检查条件逻辑

```python
# ❌ 危险
if condition and df_results:
    process(df_results)

# ✅ 安全
if condition and df_results is not None:
    process(df_results)
```

### 5. 验证 JSON 序列化

```python
# ❌ 会失败
json.dumps({"results": df})

# ✅ 必须转换
json.dumps({"results": df.to_dict('records')})
```

---

## 正确的修复示例

### 例 1: Fallback 模式

```python
# ❌ BEFORE (buggy)
all_data = df_scored or df_backup

# ✅ AFTER (safe)
all_data = df_scored
if all_data is None or (isinstance(all_data, pd.DataFrame) and all_data.empty):
    all_data = df_backup
```

### 例 2: 函数返回

```python
# ❌ BEFORE
def get_scan_results():
    df = run_scan()
    return df or {"error": "no results"}

# ✅ AFTER
def get_scan_results():
    df = run_scan()
    if df is not None and not df.empty:
        return df
    return {"error": "no results"}
```

### 例 3: API 端点

```python
# ❌ BEFORE (会在 Stage 2-3 期间导致错误)
def api_scan_status(jid):
    result = job.get("result")
    qm_all = result.get("all_scored") or result.get("all")
    return jsonify({"result": qm_all})  # 可能序列化 DataFrame！

# ✅ AFTER
def api_scan_status(jid):
    result = job.get("result")
    qm_all_source = result.get("all_scored")
    if qm_all_source is None or (isinstance(qm_all_source, pd.DataFrame) and qm_all_source.empty):
        qm_all_source = result.get("all")
    
    # 转换为JSON安全的格式
    rows = _to_rows(qm_all_source)
    return jsonify({"result": rows})
```

---

## 自动化检查

### 在本地运行检查

```bash
# 快速检查
python scripts/check_df_safety_simple.py

# 详细检查（需要 Python 3.8+）
python scripts/check_dataframe_safety.py modules/
```

### 运行完整测试

```bash
# 演示演示安全和危险操作
python tests/test_dataframe_safety_standalone.py

# 运行 pytest（如果已安装）
pytest tests/test_dataframe_safety.py -v
```

---

## 常见问题

### Q: 为什么 `if df:` 会失败？
A: pandas 不允许 DataFrame 的直接布尔转换，因为"真值"是模糊的（可能有多行）。

### Q: `df.empty` 什么时候才是安全的？
A: 必须先确认 `df is not None`，然后才能安全地访问 `.empty` 属性。

### Q: 为什么 `or` 操作这么危险？
A: Python 的 `or` 需要评估左操作数的真值。DataF Frame 拒绝这种评估。

### Q: 我应该如何处理可能返回 DataFrame 或 None 的函数？
A: 使用三级检查：1) is None，2) isinstance()，3) .empty

---

## 审查清单模板

使用此模板在 PR 注释中：

```markdown
## DataFrame Safety Review ✅

- [ ] No `if df:` or `if not df:` patterns
- [ ] No `.get(...) or df` without None check
- [ ] No `df_a if df_b else df_c` comparisons
- [ ] All `and/or df` operations properly guarded
- [ ] DataFrame returns are properly converted to dict/list
- [ ] JSON serialization handles DataFrame gracefully

**Status**: ✅ Safe / ⚠️ Needs Fixes
```

---

## 相关文档

- [问题分析报告](./DATAFRAME_TRUTHVALUE_POSTMORTEM.md) — 详细的根本原因分析
- [Python 标准指南](../.github/instructions/python-standards.instructions.md) — 编码标准
- [pandas 官方文档](https://pandas.pydata.org/docs/) — DataFrame 行为参考

---

**最后更新**: 2026-03-01 | **影响**: 高（并发扫描、JSON 序列化）
