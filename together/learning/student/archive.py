from datetime import date
from .text_pairs import title_vector,body_vector
def dated(n):
    day=n.get('published_day','')
    if not day or n.get('source_kind')=='plato_archived_copy':return n
    try:date.fromisoformat(day)
    except (TypeError,ValueError):return n
    return dict(n,publication_time=day,publication_time_is_explicit=True)

def pair_input(row,focus_features):
    a,b=dated(row['left']),dated(row['right'])
    return title_vector(a,b)+body_vector(a,b)+focus_features(dict(left=a,right=b))
