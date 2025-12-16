import difflib

_raw_text_cache = []

def is_duplicate(text):
    global _raw_text_cache
    for old_text in _raw_text_cache:
        matcher = difflib.SequenceMatcher(None, text, old_text)
        if matcher.ratio() > 0.65:
            return True
            
    _raw_text_cache.append(text)
    if len(_raw_text_cache) > 50:
        _raw_text_cache.pop(0)
    return False
