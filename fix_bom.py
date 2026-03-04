# Remove UTF-8 BOM from app.py
with open('app.py', 'rb') as f:
    content = f.read()

# Check for BOM
if content.startswith(b'\xef\xbb\xbf'):
    print("✓ Found UTF-8 BOM in app.py")
    # Remove it
    content_fixed = content[3:]
    with open('app.py', 'wb') as f:
        f.write(content_fixed)
    print("✓ BOM removed and file rewritten")
else:
    print("✗ No BOM found in app.py")

# Verify file is now valid Python
try:
    with open('app.py', 'r', encoding='utf-8') as f:
        compile(f.read(), 'app.py', 'exec')
    print("✓ app.py is now valid Python")
except SyntaxError as e:
    print(f"✗ Syntax error: {e}")
    import traceback
    traceback.print_exc()
