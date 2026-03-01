#!/usr/bin/env python3
"""
DataFrame 布尔操作安全检查工具

自动扫描 Python 代码中的危险 DataFrame 布尔操作。
可作为 pre-commit hook 或 CI/CD 检查使用。

用법:
  python scripts/check_dataframe_safety.py          # 检查所有模块
  python scripts/check_dataframe_safety.py app.py   # 检查特定文件
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent


class DataFrameSafetyChecker:
    """检测 pandas DataFrame 布尔操作的危险模式。"""
    
    # 危险模式及其说明
    UNSAFE_PATTERNS = [
        {
            'name': 'Direct DataFrame boolean cast',
            'pattern': r'if\s+(\w+_(?:df|results|rows|passed|scored|all))\s*:',
            'example': 'if df_results:',
            'fix': 'if df_results is not None and not df_results.empty:',
            'severity': 'CRITICAL'
        },
        {
            'name': 'Negated DataFrame boolean cast',
            'pattern': r'if\s+not\s+(\w+_(?:df|results|rows|passed|scored|all))\s*:',
            'example': 'if not df_results:',
            'fix': 'if df_results is None or (isinstance(df_results, pd.DataFrame) and df_results.empty):',
            'severity': 'CRITICAL'
        },
        {
            'name': 'Unsafe OR with DataFrame',
            'pattern': r'(\w+_(?:df|results|rows|passed))\s+or\s+',
            'example': 'result = df_results or fallback',
            'fix': 'result = df_results if df_results is not None else fallback',
            'severity': 'CRITICAL'
        },
        {
            'name': 'Conditional expression with DataFrame',
            'pattern': r'(\w+_(?:df|results|rows))\s+if\s+(\w+_(?:df|results|rows))\s+else',
            'example': 'result = df_a if df_b else df_c',
            'fix': 'result = df_a if (df_b is not None and not df_b.empty) else df_c',
            'severity': 'HIGH'
        }
    ]
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.issues = []
    
    def check_file(self, filepath: Path) -> List[Tuple[int, str, dict]]:
        """检查单个文件，返回问题列表。"""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"⚠️  无法读取 {filepath}: {e}")
            return []
        
        file_issues = []
        
        for line_num, line in enumerate(lines, 1):
            # 跳过注释和空行
            if line.strip().startswith('#') or not line.strip():
                continue
            
            # 检查每个危险模式
            for pattern_def in self.UNSAFE_PATTERNS:
                if re.search(pattern_def['pattern'], line):
                    # 避免误报：检查这行是否是注释或已修复的代码
                    if is_safe_context(line):
                        continue
                    
                    file_issues.append((
                        line_num,
                        line.rstrip(),
                        pattern_def
                    ))
                    
                    if self.verbose:
                        print(f"[{pattern_def['severity']}] {filepath.name}:{line_num}")
                        print(f"  Pattern: {pattern_def['name']}")
                        print(f"  Line: {line.rstrip()}")
                        print(f"  Example bad: {pattern_def['example']}")
                        print(f"  Fix: {pattern_def['fix']}\n")
        
        return file_issues
    
    def scan_directory(self, directory: Path) -> dict:
        """扫描整个目录，返回按文件分组的问题。"""
        issues_by_file = {}
        
        for py_file in sorted(directory.glob("**/*.py")):
            # 跳过测试和脚本文件（暂时）
            if 'test' in py_file.name or py_file.parent.name == '__pycache__':
                continue
            
            issues = self.check_file(py_file)
            if issues:
                issues_by_file[py_file] = issues
        
        return issues_by_file
    
    def report(self, issues_by_file: dict) -> int:
        """生成并打印报告，返回错误代码。"""
        if not issues_by_file:
            print("✅ 没有发现不安全的 DataFrame 布尔操作！")
            return 0
        
        print(f"\n{'='*80}")
        print("DataFrame 安全检查报告")
        print(f"{'='*80}\n")
        
        total_issues = sum(len(issues) for issues in issues_by_file.values())
        critical_count = 0
        high_count = 0
        medium_count = 0
        
        for filepath, issues in sorted(issues_by_file.items()):
            print(f"📄 {filepath.relative_to(ROOT)}")
            print(f"   Found {len(issues)} issue(s)\n")
            
            for line_num, line_text, pattern_def in issues:
                severity = pattern_def['severity']
                emoji = {'CRITICAL': '🔴', 'HIGH': '🟠', 'MEDIUM': '🟡'}.get(severity, '⚪')
                
                if severity == 'CRITICAL':
                    critical_count += 1
                elif severity == 'HIGH':
                    high_count += 1
                else:
                    medium_count += 1
                
                print(f"   {emoji} Line {line_num}: {pattern_def['name']}")
                print(f"      {line_text}")
                print(f"      ❌ 问题: {pattern_def['example']}")
                print(f"      ✅ 修复: {pattern_def['fix']}\n")
        
        print(f"{'='*80}")
        print(f"统计: {critical_count} CRITICAL, {high_count} HIGH, {medium_count} MEDIUM")
        print(f"总计: {total_issues} 个问题\n")
        
        return 1 if critical_count > 0 else (0 if total_count == 0 else 0)


def is_safe_context(line: str) -> bool:
    """检查该行是否在安全上下文中（已经修复或注释）。"""
    line = line.strip()
    
    # 评论
    if line.startswith('#'):
        return True
    
    # 已知安全的模式
    safe_patterns = [
        'isinstance(',
        'hasattr(',
        ' is None',
        ' is not None',
        '# ✅',
        '# SAFE',
    ]
    
    return any(pattern in line for pattern in safe_patterns)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='检查 pandas DataFrame 布尔操作安全性'
    )
    parser.add_argument(
        'files',
        nargs='*',
        help='要检查的文件或目录（默认：modules/）'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='详细输出'
    )
    
    args = parser.parse_args()
    
    checker = DataFrameSafetyChecker(verbose=args.verbose)
    
    # 确定检查路径
    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        paths = [ROOT / 'modules']
    
    all_issues = {}
    
    for path in paths:
        if path.is_file():
            issues = checker.check_file(path)
            if issues:
                all_issues[path] = issues
        elif path.is_dir():
            all_issues.update(checker.scan_directory(path))
        else:
            print(f"⚠️  未找到: {path}")
    
    return checker.report(all_issues)


if __name__ == '__main__':
    sys.exit(main())
