#!/usr/bin/env python3
"""Seed the local encomm_erp.db with realistic Greek pharmacy mock data."""
import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

# ── Bootstrap schema via DatabaseService (creates all tables + indexes) ──
from infrastructure.database_service import DatabaseService
db_service = DatabaseService()

conn = sqlite3.connect(db_service.db_path)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys=ON")

# ── Clear existing mock data ──
conn.executescript("""
    DELETE FROM invoice_items;
    DELETE FROM invoices;
    DELETE FROM ProductMaster WHERE Barcode LIKE 'MOCK-%';
    DELETE FROM customers WHERE amka LIKE 'MOCK-%';
    DELETE FROM suppliers WHERE name IN ('ΦΑΡΜΑΠΟΘΗΚΗ ΑΕ', 'SYNDESMOS ΑΕ', 'Lavipharm AE', 'ΠΡΟΜΗΘΕΥΤΙΚΗ ΕΠΕ', 'Medisyn Ltd');
""")
conn.commit()

# ── 5 Suppliers ──
suppliers = [
    ("ΦΑΡΜΑΠΟΘΗΚΗ ΑΕ", "2105551000", "orders@farmapothiki.gr", "Λεωφ. Αθηνών 120, Αθήνα"),
    ("SYNDESMOS ΑΕ", "2310555200", "info@syndesmos.gr", "Εγνατία 45, Θεσσαλονίκη"),
    ("Lavipharm AE", "2105553000", "sales@lavipharm.gr", "Αγ. Μαρίνας 65, Παιανία"),
    ("ΠΡΟΜΗΘΕΥΤΙΚΗ ΕΠΕ", "2610555400", "contact@promitheutiki.gr", "Κορίνθου 312, Πάτρα"),
    ("Medisyn Ltd", "2810555500", "hello@medisyn.gr", "Λεωφ. Κνωσού 88, Ηράκλειο"),
]
for s in suppliers:
    conn.execute("INSERT OR IGNORE INTO suppliers (name, phone, email, address) VALUES (?,?,?,?)", s)
conn.commit()
s_rows = conn.execute("SELECT id, name FROM suppliers").fetchall()
s_ids = {r["name"]: r["id"] for r in s_rows}

# ── 10 Customers ──
customers = [
    ("Γεώργιος Παπαδόπουλος", "MOCK-01018000001", "6970000001"),
    ("Μαρία Ιωάννου", "MOCK-05059000002", "6970000002"),
    ("Δημήτρης Αντωνίου", "MOCK-15038500003", "6970000003"),
    ("Ελένη Βασιλείου", "MOCK-20079200004", "6970000004"),
    ("Κωνσταντίνος Δημητρίου", "MOCK-10119500005", "6970000005"),
    ("Σοφία Νικολάου", "MOCK-25068800006", "6970000006"),
    ("Ανδρέας Χριστοδούλου", "MOCK-12027000007", "6970000007"),
    ("Αικατερίνη Παππά", "MOCK-30128200008", "6970000008"),
    ("Νικόλαος Καραγιάννης", "MOCK-18057300009", "6970000009"),
    ("Χριστίνα Οικονόμου", "MOCK-22099500010", "6970000010"),
]
for c in customers:
    conn.execute("INSERT OR IGNORE INTO customers (name, amka, phone) VALUES (?,?,?)", c)
conn.commit()
c_rows = conn.execute("SELECT id FROM customers").fetchall()
c_ids = [r["id"] for r in c_rows]

# ── 16 Products ──
products = [
    ("MOCK-001", "DEPON 500mg", 45, "2027-12-31", 3.50),
    ("MOCK-002", "PANADOL EXTRA", 12, "2027-06-15", 4.20),
    ("MOCK-003", "AUGMENTIN 625mg", 30, "2027-09-01", 8.90),
    ("MOCK-004", "XANAX 0.5mg", 8, "2027-03-15", 5.60),
    ("MOCK-005", "SALOSPIR 100mg", 60, "2028-01-10", 2.80),
    ("MOCK-006", "VOLTAREN GEL 100g", 25, "2027-08-20", 6.90),
    ("MOCK-007", "AERIUS 5mg", 15, "2027-11-05", 7.30),
    ("MOCK-008", "IMODIUM 2mg", 40, "2027-05-30", 4.50),
    ("MOCK-009", "ZYRTEC 10mg", 18, "2027-10-12", 5.20),
    ("MOCK-010", "CEFALGIN 400mg", 3, "2026-08-15", 3.90),
    ("MOCK-011", "LASIX 40mg", 22, "2027-07-25", 6.10),
    ("MOCK-012", "T4 MONTELUKAST 10mg", 14, "2027-04-18", 9.80),
    ("MOCK-013", "GLUCOPHAGE 850mg", 35, "2027-12-01", 7.60),
    ("MOCK-014", "LONARID N", 50, "2028-02-28", 3.20),
    ("MOCK-015", "BRUFEN 400mg", 7, "2027-01-20", 5.90),
    ("MOCK-016", "PONSTAN 500mg", 28, "2027-09-14", 4.80),
]
s_names = list(s_ids.keys())
for p in products:
    sid = s_ids[random.choice(s_names)]
    conn.execute(
        "INSERT OR IGNORE INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price, supplier_id) VALUES (?,?,?,?,?,?)",
        (p[0], p[1], p[2], p[3], p[4], sid))
conn.commit()

# ── 3-6 Invoices per customer (last 45 days) ──
today = datetime.now()
p_rows = conn.execute("SELECT Barcode, Price FROM ProductMaster WHERE Barcode LIKE 'MOCK-%'").fetchall()
invoice_counter = 1000

for cid in c_ids:
    num_inv = random.randint(3, 6)
    for _ in range(num_inv):
        inv_date = today - timedelta(days=random.randint(1, 45))
        inv_id = f"MOCK-INV-{invoice_counter}"
        invoice_counter += 1
        subtotal = 0.0
        items = []
        num_items = random.randint(1, 4)
        used = set()
        for __ in range(num_items):
            prod = random.choice(p_rows)
            if prod["Barcode"] in used:
                continue
            used.add(prod["Barcode"])
            qty = random.randint(1, 3)
            line_total = qty * prod["Price"]
            subtotal += line_total
            items.append((inv_id, prod["Barcode"], prod["Barcode"].replace("MOCK-", ""), qty, prod["Price"]))
        vat_amount = round(subtotal * 0.15, 2)
        grand_total = round(subtotal + vat_amount, 2)
        conn.execute(
            "INSERT INTO invoices (id, invoice_date, subtotal, vat_amount, grand_total, customer_id) VALUES (?,?,?,?,?,?)",
            (inv_id, inv_date.strftime("%Y-%m-%d"), round(subtotal, 2), vat_amount, grand_total, cid))
        for item in items:
            conn.execute(
                "INSERT INTO invoice_items (invoice_id, barcode, name, quantity, price) VALUES (?,?,?,?,?)",
                item)
        conn.commit()

inv_count = conn.execute("SELECT COUNT(*) as c FROM invoices").fetchone()["c"]
prod_count = conn.execute("SELECT COUNT(*) as c FROM ProductMaster WHERE Barcode LIKE 'MOCK-%'").fetchone()["c"]
conn.close()
print(f"✅ Seeded: {len(suppliers)} suppliers, {len(customers)} customers, {prod_count} products, {inv_count} invoices")
