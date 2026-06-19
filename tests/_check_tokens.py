import tokenize, io

with open(r'C:\Users\xampos\Desktop\ERP\infrastructure\database_service.py', 'rb') as f:
    tokens = list(tokenize.tokenize(f.readline))
    print(f"Total tokens: {len(tokens)}")
    # Find any ERRORTOKEN
    for tok in tokens:
        if tok.type == tokenize.ERRORTOKEN:
            print(f"ERROR at line {tok.start[0]}: {tok.string[:100]}")
    print("Done checking tokens")
