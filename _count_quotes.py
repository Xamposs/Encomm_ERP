with open(r'C:\Users\xampos\Desktop\ERP\infrastructure\database_service.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

count = 0
for i, line in enumerate(lines, 1):
    n = line.count('"""')
    if n:
        count += n
        print(f"Line {i}: {n} occurrences (running total: {count}) -> {line.rstrip()[:120]}")

print(f"\nTotal: {count} (should be even)")
