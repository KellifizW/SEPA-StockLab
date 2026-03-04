#!/usr/bin/env python
import ast
import sys

with open('app.py', 'r', encoding='utf-8') as f:
    tree = ast.parse(f.read())

print("Analyzing app.py AST structure...\n")

# Find all FunctionDef nodes at module level
module_level_funcs = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]

target_funcs = ['htmx_dashboard_highlights', 'api_ibkr_connect', 'api_ibkr_status']

print("Target functions at module level:")
for name in target_funcs:
    status = '✓' if name in module_level_funcs else '✗'
    print(f"  {status} {name}")

print(f"\nTotal module-level functions: {len(module_level_funcs)}")

# Check if these functions are inside other blocks
print("\nChecking for decorators on these functions...")
for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        if node.name in target_funcs:
            has_decorators = len(node.decorator_list) > 0
            decorators = [ast.unparse(d) if hasattr(ast, 'unparse') else str(d) for d in node.decorator_list]
            print(f"\n{node.name}:")
            print(f"  Has decorators: {has_decorators}")
            if decorators:
                for dec in decorators:
                    print(f"    @{dec}")
            print(f"  Line: {node.lineno}")
