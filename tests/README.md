# Test Runner Script

Run tests:
```bash
cd /home/sven/Schreibtisch/klipper_nas_script

# Install test dependencies
pip3 install -r tests/requirements.txt

# Run all tests
python3 -m pytest tests/ -v

# Run specific test file
python3 -m pytest tests/test_path_utils.py -v

# Run with coverage
python3 -m pytest tests/ --cov=scripts --cov-report=html
```

## Test Coverage

Current test files:
- `test_path_utils.py` - Path sanitization tests
- `test_metadata_cache.py` - SQLite cache tests

## TODO: Integration Tests

Integration tests require:
- Real NFS mount
- Moonraker instance
- Test G-code files

See `implementation_plan.md` for full test strategy.
