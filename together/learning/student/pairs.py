import numpy as np
from urllib.parse import urlsplit
from .extended import extended
from .evidence import extra
def txt(n):return n['title']+' '+n['title']+' '+n.get('body','')[:6500]

def org(n):
 u=urlsplit(n.get('source_url',''));return (u.hostname or '')+' '+u.path.split('/')[1] if '/' in u.path else u.hostname or ''

def pairfeat(r,em):
 a,b=r['left'],r['right'];x,y=em[0],em[1]
 return extended(r)+extra(r)+[org(a)==org(b),float(x@y),float(np.abs(x-y).mean())]+list(x)+list(y)+list(np.abs(x-y))+list(x*y)
