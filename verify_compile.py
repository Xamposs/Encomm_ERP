import py_compile
try:
    py_compile.compile('presentation/main_window.py', doraise=True)
    print('OK: main_window.py compiles successfully')
except py_compile.PyCompileError as e:
    print(f'COMPILE ERROR: {e}')
