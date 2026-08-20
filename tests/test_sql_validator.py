from backend.validators import validate_select_sql


def test_valid_select_gets_limit():
    is_valid, reason, sql = validate_select_sql("SELECT name FROM products")

    assert is_valid is True
    assert reason is None
    assert sql == "SELECT name FROM products LIMIT 100"


def test_blocks_delete_statement():
    is_valid, reason, sql = validate_select_sql("DELETE FROM products")

    assert is_valid is False
    assert "Only SELECT" in reason
    assert sql is None


def test_blocks_multiple_statements():
    is_valid, reason, sql = validate_select_sql("SELECT * FROM products; DROP TABLE products")

    assert is_valid is False
    assert "one SQL statement" in reason
    assert sql is None


def test_blocks_select_that_does_not_read_retail_data():
    is_valid, reason, sql = validate_select_sql("SELECT 'Riyadh' AS answer")

    assert is_valid is False
    assert "must read from" in reason
    assert sql is None


def test_allows_select_on_multiple_lines():
    is_valid, reason, sql = validate_select_sql("SELECT\nname\nFROM products")

    assert is_valid is True
    assert reason is None
    assert sql == "SELECT\nname\nFROM products LIMIT 100"
