#!/usr/bin/env python3
"""
DataFrame 布尔操作安全性单元测试

验证：
- 危险操作会失败
- 安全操作有效
- 错误处理工作正常
"""

import pytest
import pandas as pd
import numpy as np


class TestDataFrameBooleanSafety:
    """验证 DataFrame 布尔转换安全性。"""
    
    def test_dataframe_direct_bool_raises_error(self):
        """直接 bool() 转换会抛出 ValueError。"""
        df = pd.DataFrame({"a": [1, 2, 3]})
        
        # 直接布尔转换应该失败
        with pytest.raises(ValueError, match="ambiguous"):
            if df:
                pass
    
    def test_dataframe_negated_bool_raises_error(self):
        """直接加 not 的布尔转换也会失败。"""
        df = pd.DataFrame({"a": [1, 2, 3]})
        
        with pytest.raises(ValueError, match="ambiguous"):
            if not df:
                pass
    
    def test_dataframe_or_operation_raises_error(self):
        """OR 操作会触发布尔强制转换。"""
        df = pd.DataFrame({"a": [1, 2, 3]})
        
        with pytest.raises(ValueError, match="ambiguous"):
            result = df or {}
    
    def test_dataframe_and_operation_raises_error(self):
        """AND 操作也会触发布尔强制。"""
        df = pd.DataFrame({"a": [1, 2, 3]})
        
        with pytest.raises(ValueError, match="ambiguous"):
            if True and df:
                pass
    
    def test_conditional_expression_with_dataframe_raises_error(self):
        """条件表达式中的 DataFrame 比较会失败。"""
        df_a = pd.DataFrame({"a": [1]})
        df_b = pd.DataFrame({"b": [2]})
        
        with pytest.raises(ValueError, match="ambiguous"):
            result = df_a if df_b else {}
    
    # ------- 安全的做法 -------
    
    def test_safe_none_check(self):
        """None 检查很安全。"""
        df = None
        
        if df is None:
            result = "empty"
        else:
            result = "has_data"
        
        assert result == "empty"
    
    def test_safe_none_and_empty_check(self):
        """None + empty 检查是安全的。"""
        df_none = None
        df_empty = pd.DataFrame()
        df_nonempty = pd.DataFrame({"a": [1]})
        
        # None check first
        assert df_none is None
        
        # Empty check on DataFrame
        assert df_empty.empty
        assert not df_nonempty.empty
        
        # Combined check (SAFE)
        if df_none is not None and not df_none.empty:
            raise AssertionError("Should not reach here")
    
    def test_safe_or_with_none_check(self):
        """在 None 检查后，OR 操作很安全。"""
        df = None
        result = df or {}
        assert result == {}
    
    def test_safe_isinstance_check(self):
        """isinstance() 检查是完全安全的。"""
        df = pd.DataFrame({"a": [1, 2, 3]})
        
        if isinstance(df, pd.DataFrame):
            result = "is_dataframe"
        else:
            result = "not_dataframe"
        
        assert result == "is_dataframe"
    
    def test_safe_hasattr_empty_check(self):
        """hasattr() + .empty 检查很安全。"""
        df = pd.DataFrame({"a": [1]})
        
        if hasattr(df, "empty") and df.empty:
            result = "empty"
        else:
            result = "not_empty"
        
        assert result == "not_empty"
    
    def test_safe_len_check(self):
        """len() 检查也很安全。"""
        df = pd.DataFrame({"a": [1, 2]})
        
        if len(df) > 0:
            result = "has_rows"
        else:
            result = "no_rows"
        
        assert result == "has_rows"


class TestDataFrameFallbackPatterns:
    """测试安全的 fallback 模式。"""
    
    def test_unsafe_pattern_returns_dataframe_or_dict(self):
        """❌ 这个模式会失败。"""
        def get_data_unsafe(condition):
            if condition:
                return pd.DataFrame({"a": [1, 2]})
            else:
                return {}
        
        df = get_data_unsafe(True)
        # 这会失败，因为 df 不能直接用在 or 中
        with pytest.raises(ValueError, match="ambiguous"):
            result = df or {"default": True}
    
    def test_safe_pattern_explicit_fallback(self):
        """✅ 显式检查后再做 fallback。"""
        def get_data_safe(condition):
            df = pd.DataFrame({"a": [1, 2]}) if condition else None
            return df if df is not None else {}
        
        result_with_data = get_data_safe(True)
        result_without_data = get_data_safe(False)
        
        assert isinstance(result_with_data, pd.DataFrame)
        assert isinstance(result_without_data, dict)
    
    def test_safe_pattern_with_get_methods(self):
        """✅ dict.get() 加上安全的 fallback。"""
        results = {
            "all_scored": pd.DataFrame({"a": [1, 2, 3]}),
            "all": None
        }
        
        # ❌ 危险的做法
        with pytest.raises(ValueError, match="ambiguous"):
            data = results.get("all_scored") or results.get("all")
        
        # ✅ 安全的做法
        data = results.get("all_scored")
        if data is None or (isinstance(data, pd.DataFrame) and data.empty):
            data = results.get("all")
        
        assert isinstance(data, pd.DataFrame)


class TestJSONSerializationSafety:
    """测试 DataFrame 在 JSON 序列化中的安全性。"""
    
    def test_dataframe_in_dict_causes_serialization_error(self):
        """DataFrame 在 dict 中会导致 JSON 序列化失败。"""
        import json
        
        data = {
            "results": pd.DataFrame({"a": [1, 2]}),
            "count": 2
        }
        
        with pytest.raises(TypeError):
            json.dumps(data)
    
    def test_dataframe_needs_conversion_before_json(self):
        """DataFrame 必须转换后才能序列化。"""
        import json
        
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4.5, np.nan, 6.7]})
        
        # ✅ 转换为字典列表
        data = {
            "results": df.to_dict('records'),
            "count": len(df)
        }
        
        json_str = json.dumps(data)
        assert '"a": 1' in json_str or '"a":1' in json_str
    
    def test_sanitization_removes_dataframes(self):
        """清理函数应该删除 DataFrame。"""
        
        def sanitize(obj, depth=0, max_depth=5):
            if depth > max_depth:
                return None
            if obj is None or isinstance(obj, (bool, int, str)):
                return obj
            if isinstance(obj, float):
                if pd.isna(obj) or np.isnan(obj):
                    return None
                if np.isinf(obj):
                    return str(obj)
                return float(obj)
            if isinstance(obj, pd.DataFrame):
                return None  # ✅ 删除 DataFrame
            if isinstance(obj, list):
                return [sanitize(item, depth+1, max_depth) for item in obj]
            if isinstance(obj, dict):
                return {k: sanitize(v, depth+1, max_depth) 
                        for k, v in obj.items()}
            return str(obj)
        
        data = {
            "df": pd.DataFrame({"a": [1]}),
            "value": 42,
            "nan": float('nan'),
            "infinity": float('inf'),
            "nested": {
                "df": pd.DataFrame(),
                "count": 10
            }
        }
        
        sanitized = sanitize(data)
        
        # DataFrame 被删除了
        assert sanitized["df"] is None
        assert sanitized["value"] == 42
        assert sanitized["nan"] is None
        assert sanitized["infinity"] == "inf"
        assert sanitized["nested"]["df"] is None
        assert sanitized["nested"]["count"] == 10
        
        # 现在应该能序列化
        import json
        json_str = json.dumps(sanitized)
        assert len(json_str) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
