def normalize_person_name(value):
    """Trim and collapse whitespace, then capitalize each name word."""
    if value is None:
        return None
    return " ".join(word[:1].upper() + word[1:].lower() for word in str(value).split())
