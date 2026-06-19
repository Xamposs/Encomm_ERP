import ast, sys
try:
    with open(r'C:\Users\xampos\Desktop\ERP\infrastructure\database_service.py', encoding='utf-8') as f:
        ast.parse(f.read())
    print("AST parse OK")
except SyntaxError as e:
    print(f"SyntaxError: {e}")
