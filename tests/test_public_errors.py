from ngx_research.services.public_errors import public_error_message


def test_public_error_message_hides_database_internals():
    raw_error = RuntimeError(
        '(psycopg.errors.UniqueViolation) duplicate key value violates unique constraint '
        '"uq_bond_auction_date_instrument_tenor" DETAIL: Key already exists. [SQL: INSERT ...]'
    )

    message = public_error_message(raw_error, action="run automatic market intelligence")

    assert "psycopg" not in message.lower()
    assert "unique constraint" not in message.lower()
    assert "[sql:" not in message.lower()
    assert message == "EquityKobo could not run automatic market intelligence. Please try again shortly."
