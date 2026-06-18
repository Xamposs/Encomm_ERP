"""Quick smoke test: verify new DatabaseService methods exist with correct signatures."""
from infrastructure.database_service import DatabaseService
import inspect

methods = {
    'get_config_typed':   ['self', 'key', 'default', 'type_fn'],
    'seed_default_config': ['self', 'defaults'],
    'config_exists':      ['self', 'key'],
    'bulk_set_config':    ['self', 'items'],
    '_verify_wal':        ['self', 'conn'],
}

for name, expected_params in methods.items():
    assert hasattr(DatabaseService, name), f'MISSING: {name}'
    sig = inspect.signature(getattr(DatabaseService, name))
    actual_params = list(sig.parameters.keys())
    assert actual_params == expected_params, \
        f'{name}: expected params {expected_params}, got {actual_params}'
    print(f'  ✅ {name}{sig}')

print()
print('All 5 methods present with correct signatures.')

# Check source for seed_default_config's SQL pattern
source = inspect.getsource(getattr(DatabaseService, 'seed_default_config'))
assert 'INSERT OR IGNORE' in source, 'seed_default_config should use INSERT OR IGNORE'
assert 'BEGIN TRANSACTION' in source, 'seed_default_config should use explicit transaction'
assert 'rollback' in source, 'seed_default_config should have rollback on failure'
print('  ✅ seed_default_config uses INSERT OR IGNORE + explicit transaction + rollback')

source = inspect.getsource(getattr(DatabaseService, 'bulk_set_config'))
assert 'ON CONFLICT' in source, 'bulk_set_config should use ON CONFLICT upsert'
assert 'executemany' in source, 'bulk_set_config should use executemany'
print('  ✅ bulk_set_config uses executemany + ON CONFLICT upsert')

source = inspect.getsource(getattr(DatabaseService, '_verify_wal'))
assert 'PRAGMA journal_mode' in source, '_verify_wal should check PRAGMA journal_mode'
print('  ✅ _verify_wal checks PRAGMA journal_mode')

source = inspect.getsource(getattr(DatabaseService, 'get_config_typed'))
assert 'type_fn' in source, 'get_config_typed should use type_fn'
assert 'logging.warning' in source, 'get_config_typed should log warning on failure'
print('  ✅ get_config_typed uses type_fn with warning logging')

source = inspect.getsource(getattr(DatabaseService, 'config_exists'))
assert 'SELECT 1' in source, 'config_exists should use SELECT 1'
print('  ✅ config_exists uses SELECT 1')

# Check _verify_wal is called in _initialize_db
source_init = inspect.getsource(getattr(DatabaseService, '_initialize_db'))
assert 'self._verify_wal(conn)' in source_init, '_initialize_db must call _verify_wal'
print('  ✅ _initialize_db calls _verify_wal(conn)')

print()
print('All assertions passed.')
