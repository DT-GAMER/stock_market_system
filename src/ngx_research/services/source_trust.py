TRUSTED_DOCUMENT_TYPES = {
    "ngxpulse_market_data",
}


def is_trusted_document_type(document_type: str | None) -> bool:
    if not document_type:
        return False
    return document_type in TRUSTED_DOCUMENT_TYPES
